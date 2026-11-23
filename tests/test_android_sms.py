from sofos.messages import android_sms

OUTPUT = """Row: 0 _id=3, thread_id=7, address=+573005556677, date=1700000300000, type=1, body=línea uno
línea dos, con coma
Row: 1 _id=2, thread_id=7, address=+573005556677, date=1700000200000, type=2, body=te llamo luego
Row: 2 _id=1, thread_id=9, address=Banco, date=1700000100000, type=1, body=Su código es 1234
"""


def test_parse_multiline_bodies():
    rows = android_sms.parse_rows(OUTPUT)
    assert len(rows) == 3
    assert rows[0]["body"] == "línea uno\nlínea dos, con coma"


def test_conversations_and_messages():
    rows = android_sms.parse_rows(OUTPUT)
    convs = android_sms.to_conversations(rows)
    assert [(c.id, c.title, c.message_count) for c in convs] == [
        ("7", "+573005556677", 2),
        ("9", "Banco", 1),
    ]
    msgs = android_sms.to_messages(rows, "7", limit=100, before=None)
    assert [m.text for m in msgs] == ["te llamo luego", "línea uno\nlínea dos, con coma"]
    assert [m.from_me for m in msgs] == [True, False]
