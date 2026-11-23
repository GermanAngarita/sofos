from sofos.messages import apple_db


def test_conversations_sorted_by_last_message(chat_db):
    convs = apple_db.list_conversations(chat_db)
    assert [c.title for c in convs] == ["Familia", "+573001112233"]
    assert convs[0].message_count == 2
    assert convs[0].last_message == "📎 Adjunto"
    assert convs[1].last_message == "Hola!"  # sacado de attributedBody
    assert round(convs[1].last_date) == 1_700_000_200


def test_messages_in_order_with_pagination(chat_db):
    msgs = apple_db.list_messages(chat_db, "10")
    assert [m.text for m in msgs] == ["hola", "qué tal", "Hola!"]
    assert [m.from_me for m in msgs] == [False, True, False]
    assert msgs[0].sender == "+573001112233" and msgs[1].sender == "Yo"

    older = apple_db.list_messages(chat_db, "10", before=1_700_000_200)
    assert [m.text for m in older] == ["hola", "qué tal"]
    assert [m.text for m in apple_db.list_messages(chat_db, "10", limit=1)] == ["Hola!"]


def test_decode_attributed_body_handles_garbage():
    assert apple_db.decode_attributed_body(None) is None
    assert apple_db.decode_attributed_body(b"sin marcador") is None
