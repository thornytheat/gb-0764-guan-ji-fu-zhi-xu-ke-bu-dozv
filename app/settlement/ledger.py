import json

from app.problem import DeskError

PERIOD_LEN = 7  # YYYY-MM


def month_of(day):
    return day[:PERIOD_LEN]


def check_period(period):
    if len(period) != PERIOD_LEN or period[4] != '-':
        raise DeskError('PERIOD_FORMAT')
    year, dash, month = period.partition('-')
    if not (year.isdigit() and month.isdigit() and 1 <= int(month) <= 12 and len(month) == 2):
        raise DeskError('PERIOD_FORMAT')
    return period


def load_volumes(conn):
    """取全部已归卷批次；同 run 去重。撤销的来件日期回 intake 订单补齐。"""
    rows = conn.execute('SELECT manifest, evidence FROM volumes ORDER BY rowid').fetchall()
    seen_runs, ledgers = set(), []
    for manifest_text, evidence_text in rows:
        manifest, evidence = json.loads(manifest_text), json.loads(evidence_text)
        if manifest['run_id'] in seen_runs:
            continue
        seen_runs.add(manifest['run_id'])
        received_at = {order['entry']: order['received']
                       for order in evidence['intake']['orders']}
        reversals = []
        for rev in evidence['pages']['reversals']:
            reversals.append({**rev, 'received': received_at.get(rev['entry'])})
        ledgers.append({'run_id': manifest['run_id'],
                        'copies': evidence['pages']['copies'],
                        'reversals': reversals})
    return ledgers


def monthly_gross(ledgers):
    """按来件月归据实签页；同流水号多批次复跑只采信一份（草卷、隔离件不在 copies 内）。"""
    chosen, gross = {}, {}
    for ledger in ledgers:
        for row in ledger['copies']:
            period = month_of(row['received'])
            if row['licensed'] <= 0:
                continue
            key = row['entry']
            if key not in chosen:
                chosen[key] = (period, row['licensed'])
                gross[period] = gross.get(period, 0) + row['licensed']
    return gross, chosen


def target_lookup(ledgers):
    """原凭证实签页索引：撤销只允许抵原件真实许可的页。"""
    targets = {}
    for ledger in ledgers:
        for row in ledger['copies']:
            if row['licensed'] > 0 and row['entry'] not in targets:
                targets[row['entry']] = {'licensed': row['licensed'],
                                         'period': month_of(row['received']),
                                         'run_id': ledger['run_id']}
    return targets
