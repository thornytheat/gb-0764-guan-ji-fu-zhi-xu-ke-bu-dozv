from datetime import datetime
import json
import sqlite3

from app.problem import DeskError
from .ledger import check_period, load_volumes, monthly_gross, month_of, target_lookup


def now_iso():
    return datetime.now().astimezone().isoformat()


def this_month():
    return datetime.now().strftime('%Y-%m')


def existing_close(conn, period):
    row = conn.execute('SELECT body FROM closes WHERE period=?', (period,)).fetchone()
    return json.loads(row[0]) if row else None


def successful_reversals(ledgers):
    """通过裁决且确实返页的撤销，保留来件日期与行号；同流水号只取首次成功件。"""
    picked = {}
    for ledger in ledgers:
        for rev in ledger['reversals']:
            if rev['reason'] or rev['restored'] <= 0:
                continue
            if rev['entry'] not in picked:
                picked[rev['entry']] = {**rev, 'run_id': ledger['run_id']}
    return picked


def internal_offsets(ledgers, period):
    """同月内完成的撤销：在当月毛额内冲减，不产生跨期 posting。"""
    total, entries = 0, set()
    for rev in successful_reversals(ledgers).values():
        if month_of(rev['received']) == period:
            total += rev['restored']
            entries.add(rev['entry'])
    return total, sorted(entries)


def settled_entries(conn):
    """此前结账已处理掉的撤销流水号（含当月冲减与已跨期落账），保证一笔只落一个月。"""
    done = {row[0] for row in conn.execute('SELECT entry FROM postings').fetchall()}
    for (body,) in conn.execute('SELECT body FROM closes').fetchall():
        report = json.loads(body)
        done.update(report.get('same_period_cancels', ()))
        done.update(report.get('late_cancels', ()))
    return done


def late_postings(conn, period, ledgers, closed_months):
    """晚到撤销：来源月已结账、本批次在结账后才出现，落在当前处理月，保留来件日期。"""
    targets = target_lookup(ledgers)
    already = settled_entries(conn)
    items = []
    for rev in successful_reversals(ledgers).values():
        origin = month_of(rev['received'])
        if rev['entry'] in already or origin not in closed_months or origin >= period:
            continue
        target = targets.get(rev['target'])
        if target is None:
            continue
        items.append({'entry': rev['entry'], 'target': rev['target'], 'period': period,
                      'source_date': rev['received'], 'origin_period': origin,
                      'pages': min(rev['restored'], target['licensed']),
                      'run_id': rev['run_id'], 'line': rev['line']})
    return items


def freeze(conn, period, as_of=None):
    """结一笔月账：冻结原报数字，冲减归入唯一处理月。返回 (报告, 是否复用旧结账)。"""
    check_period(period)
    if as_of is not None:
        try:
            datetime.strptime(as_of, '%Y-%m-%d')
        except ValueError as exc:
            raise DeskError('PERIOD_FORMAT') from exc
    if period > (as_of or this_month()):
        raise DeskError('FUTURE_PERIOD')
    already = existing_close(conn, period)
    if already is not None:
        return already, True
    closed_rows = conn.execute('SELECT period FROM closes').fetchall()
    closed_months = {row[0] for row in closed_rows}
    ledgers = load_volumes(conn)
    gross, chosen = monthly_gross(ledgers)
    # 有实签数据的更早月份若未结账，不允许跳月
    if any(month < period and month not in closed_months and gross.get(month, 0) > 0
           for month in gross):
        raise DeskError('PERIOD_ORDER')
    inside, inside_entries = internal_offsets(ledgers, period)
    late = late_postings(conn, period, ledgers, closed_months)
    late_total = sum(item['pages'] for item in late)
    body = {'period': period, 'closed_at': now_iso(),
            'gross_signed': gross.get(period, 0),
            'same_period_offsets': inside,
            'late_postings': late_total,
            'net_signed': gross.get(period, 0) - inside - late_total,
            'orders': sorted(entry for entry, (month, _) in chosen.items() if month == period),
            'same_period_cancels': inside_entries,
            'late_cancels': [item['entry'] for item in late]}
    try:
        conn.execute('INSERT INTO closes (period, body, closed_at) VALUES (?,?,?)',
                     (period, json.dumps(body, ensure_ascii=False), body['closed_at']))
        for item in late:
            conn.execute(
                'INSERT INTO postings (entry,target,period,source_date,posted_at,pages,run_id,line) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (item['entry'], item['target'], period, item['source_date'], body['closed_at'],
                 item['pages'], item['run_id'], item['line']))
    except sqlite3.IntegrityError as exc:
        if existing_close(conn, period) is not None:
            return existing_close(conn, period), True
        raise DeskError('PERIOD_BUSY') from exc
    return body, False


def record_late(conn, period, reversal, target, run_id):
    """晚到撤销落当前（未结）处理月。entry 主键保证并发与重试只抵一次，月份不漂移。"""
    check_period(period)
    origin = month_of(reversal['received'])
    if existing_close(conn, period) is not None or origin >= period \
            or existing_close(conn, origin) is None:
        raise DeskError('PERIOD_ORDER')
    pages = min(reversal['restored'], target['licensed'])
    record = (reversal['entry'], reversal['target'], period, reversal['received'],
              now_iso(), pages, run_id, reversal['line'])
    try:
        conn.execute(
            'INSERT INTO postings (entry,target,period,source_date,posted_at,pages,run_id,line) '
            'VALUES (?,?,?,?,?,?,?,?)', record)
    except sqlite3.IntegrityError:
        row = conn.execute('SELECT period, pages FROM postings WHERE entry=?',
                           (reversal['entry'],)).fetchone()
        return {'entry': reversal['entry'], 'target': reversal['target'], 'period': row[0],
                'pages': row[1], 'reused': True}
    return {'entry': reversal['entry'], 'target': reversal['target'], 'period': period,
            'pages': pages, 'reused': False}


def list_closes(conn):
    return [json.loads(row[0]) for row in
            conn.execute('SELECT body FROM closes ORDER BY period DESC').fetchall()]


def list_postings(conn, period=None):
    sql = 'SELECT entry,target,period,source_date,posted_at,pages,run_id,line FROM postings'
    params = ()
    if period is not None:
        sql += ' WHERE period=?'
        params = (period,)
    sql += ' ORDER BY period, source_date'
    keys = ('entry', 'target', 'period', 'source_date', 'posted_at', 'pages', 'run_id', 'line')
    return [dict(zip(keys, row)) for row in conn.execute(sql, params).fetchall()]
