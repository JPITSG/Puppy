"""Completed session-exchange state shared by snapshot coverage tests."""
def completed_records(node, source_id, target_id):
    source, target = "{}/{}".format(node, source_id), "{}/{}".format(node, target_id)
    rid, child, workflow = "1" * 32, "2" * 32, "3" * 32
    spec = {"id": "review", "refs": [target], "action": "task", "text": "Review the change", "after": []}
    receipt = {"format": 1, "id": child, "source": source, "source_title": "Origin",
               "target": target, "action": "task", "text": spec["text"],
               "deadline": 1700003600.0, "expected_turn_id": "", "created_at": 1700000000.0,
               "status": "completed", "prompt": "A recorded request", "turn_id": "finished-turn",
               "start_seq": 1, "end_seq": 3, "answer": "Review passed", "error": ""}
    result = {key: value for key, value in receipt.items() if key in
              {"id", "source", "target", "action", "status", "created_at", "deadline", "turn_id", "start_seq", "end_seq", "answer", "error"}}
    signature = {"refs": [target], "action": "task", "text": spec["text"], "timeout_s": 3600, "expected_turn_ids": {}}
    request = {"format": 1, "id": rid, "source": source, "source_title": "Origin",
               "signature": signature, "action": "task", "text": spec["text"],
               "targets": [target], "created_at": 1700000000.0, "deadline": 1700003600.0,
               "status": "completed", "results": [result], "unavailable": [], "workflow": workflow, "notified": True}
    plan = {"format": 1, "id": workflow, "source": source, "title": "Review",
            "signature": {"title": "Review", "steps": [spec], "timeout_s": 3600},
            "created_at": 1700000000.0, "deadline": 1700003600.0, "status": "completed",
            "steps": [{"spec": spec, "status": "completed", "request": rid,
                       "results": [result], "error": "", "dispatch": None}],
            "unavailable": [], "notified": True}
    return {"session_references.{}".format(source_id): {"format": 1, "controller": node, "refs": [target]},
            "session_inbox." + child: receipt, "session_outbox." + rid: request,
            "session_workflow." + workflow: plan}
