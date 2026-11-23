from concurrent.futures import ThreadPoolExecutor

from app.main import create_app
from app.source import bundle
from app.settlement.close import freeze
from app.vault.record import deposit


HEAD = 'entry,action,received,institution,issn,pages,target\n'


def post_volume(client, text):
    return client.post('/api/volumes', json={'csv': text}).json


def close(client, period, as_of=None):
    return client.post('/api/closes', json={'period': period, 'as_of': as_of})


def test_draft_and_quarantined_material_never_enters_monthly_books(client):
    """草卷（未归卷）与隔离、重复、零许可件都不产生实签页。"""
    post_volume(client, bundle('dirty').decode())
    reply = close(client, '2026-11', as_of='2026-11-30')
    body = reply.json
    assert reply.status_code == 201
    # 11 月毛实签 = 6+4+6+3+3+2 = 24；同月 R01 抵 C01 的 6 页 → 净 18
    assert (body['gross_signed'], body['same_period_offsets'],
            body['late_postings'], body['net_signed']) == (24, 6, 0, 18)
    # 零许可的 C04/C06/C08/C09 与隔离件不进订单清单
    assert set(body['orders']) == {'C01', 'C02', 'C03', 'C07', 'C10', 'C11'}
    assert body['same_period_cancels'] == ['R01']


def test_late_cancellation_keeps_source_date_but_posts_to_current_month(client):
    post_volume(client, bundle('dirty').decode())
    first = close(client, '2026-11', as_of='2026-11-30').json
    # 12 月才收到、来件日期写 11 月的撤销，目标为 11 月已许可 6 页的 C03
    amended = bundle('dirty').decode() + 'X2,cancel,2026-11-12,00123,,,C03\n'
    post_volume(client, amended)
    december = close(client, '2026-12', as_of='2026-12-31').json

    # 11 月原报数字原封不动
    again = close(client, '2026-11', as_of='2026-12-31')
    assert again.status_code == 200 and again.json == first
    # 撤销只抵原凭证实签页（C03 实签 6），保留来件日期，落在 12 月
    postings = client.get('/api/closes/2026-12').json['postings']
    assert postings == [{**postings[0], 'entry': 'X2', 'target': 'C03', 'period': '2026-12',
                         'source_date': '2026-11-12', 'pages': 6}]
    assert postings[0]['source_date'].startswith('2026-11') and postings[0]['period'] == '2026-12'
    # 12 月毛 8（C05+C15），晚到冲减 6 → 净 2；R01 不会被二次算作晚到冲减
    assert (december['gross_signed'], december['late_postings'],
            december['net_signed'], december['late_cancels']) == (8, 6, 2, ['X2'])


def test_offset_never_exceeds_original_licensed_pages(client):
    post_volume(client, bundle('dirty').decode())
    close(client, '2026-11', as_of='2026-11-30')
    # C07 实签只有 3 页，撤销返页被夹到原凭证实签页
    amended = bundle('dirty').decode() + 'X3,cancel,2026-11-13,34001,,,C07\n'
    post_volume(client, amended)
    body = close(client, '2026-12', as_of='2026-12-31').json
    posting = client.get('/api/closes/2026-12').json['postings'][0]
    assert posting['pages'] == 3 and body['late_postings'] == 3


def test_retry_same_incoming_docket_does_not_double_credit(client, desk):
    post_volume(client, bundle('dirty').decode())
    close(client, '2026-11', as_of='2026-11-30')
    amended = bundle('dirty').decode() + 'X2,cancel,2026-11-12,00123,,,C03\n'
    post_volume(client, amended)
    close(client, '2026-12', as_of='2026-12-31')
    # 整卷重发、重复归卷、重复结账
    post_volume(client, amended)
    post_volume(client, amended)
    close(client, '2026-12', as_of='2027-01-05')
    with desk.vault.session() as conn:
        rows = conn.execute('SELECT entry, period, pages FROM postings').fetchall()
    assert rows == [('X2', '2026-12', 6)]


def test_concurrent_close_and_cancellation_land_in_one_month_only(desk):
    deposit(desk.vault, bundle('dirty'))
    with desk.vault.session() as conn:
        freeze(conn, '2026-11', as_of='2026-11-30')
    amended = bundle('dirty') + b'X2,cancel,2026-11-12,00123,,,C03\n'

    def settle(_):
        with desk.vault.session() as conn:
            return freeze(conn, '2026-12', as_of='2026-12-31')

    # 含晚到撤销的卷宗先落库，再让多个关账动作并发抢同一笔冲减
    deposit(desk.vault, amended)
    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(settle, range(16)))
    reused = sum(item[1] for item in outcomes)
    assert reused == 15 and sum(not item[1] for item in outcomes) == 1
    with desk.vault.session() as conn:
        postings = conn.execute('SELECT entry, period, pages FROM postings').fetchall()
        closes = conn.execute('SELECT period FROM closes').fetchall()
    assert postings == [('X2', '2026-12', 6)]
    assert sorted(row[0] for row in closes) == ['2026-11', '2026-12']


def test_closed_month_keeps_original_reported_figures(client):
    post_volume(client, bundle('dirty').decode())
    close(client, '2026-11', as_of='2026-11-30')
    original = client.get('/api/closes/2026-11').json['close']
    # 之后无论再来多少撤销，已结月份的查询结果不变
    post_volume(client, bundle('dirty').decode() + 'X2,cancel,2026-11-12,00123,,,C03\n')
    close(client, '2026-12', as_of='2026-12-31')
    assert client.get('/api/closes/2026-11').json['close'] == original
    index = {item['period']: item for item in client.get('/api/closes').json['closes']}
    assert index['2026-11']['net_signed'] == 18 and index['2026-12']['net_signed'] == 2


def test_period_rules_and_unknown_month(client):
    assert close(client, '2026-13').status_code == 422
    assert close(client, '11-2026').json['error'] == 'PERIOD_FORMAT'
    assert close(client, '2027-01', as_of='2026-11-30').json['error'] == 'FUTURE_PERIOD'
    assert client.get('/api/closes/2026-09').status_code == 404
    post_volume(client, bundle('dirty').decode())
    # 不能跳过未结月份
    assert close(client, '2026-12', as_of='2026-12-31').json['error'] == 'PERIOD_ORDER'


def test_failed_cancellations_are_not_posted(client):
    post_volume(client, bundle('dirty').decode())
    close(client, '2026-11', as_of='2026-11-30')
    close(client, '2026-12', as_of='2026-12-31')
    # R02/R03/R04 均为失败撤销（错误机构、重复、无目标），不产生任何冲减
    assert client.get('/api/closes').json['postings'] == []


def test_record_late_pins_month_and_survives_retry(desk):
    from app.settlement.close import freeze, record_late
    from app.settlement.ledger import load_volumes, target_lookup
    from app.vault.record import deposit
    deposit(desk.vault, bundle('dirty'))
    with desk.vault.session() as conn:
        freeze(conn, '2026-11', as_of='2026-11-30')
        ledgers = load_volumes(conn)
        target = target_lookup(ledgers)['C03']
        reversal = {'entry': 'X9', 'target': 'C03', 'received': '2026-11-20',
                    'restored': 6, 'line': 99}
        first = record_late(conn, '2026-12', reversal, target, ledgers[0]['run_id'])
        retry = record_late(conn, '2026-12', reversal, target, ledgers[0]['run_id'])
        other_month = record_late(conn, '2027-01', reversal, target, ledgers[0]['run_id'])
        count = conn.execute('SELECT COUNT(*) FROM postings WHERE entry=?', ('X9',)).fetchone()[0]
    assert first['reused'] is False
    assert retry == {**first, 'reused': True}
    # 换月重试不漂移：同一笔冲减只落在 12 月
    assert other_month['reused'] is True and other_month['period'] == '2026-12'
    assert count == 1
