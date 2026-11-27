#!/bin/bash
cat << 'PYEOF' > /app/process.py
import json

with open('/app/invoices/batch_01.json') as f:
    items = json.load(f)

with open('/app/config/limits.json') as f:
    cfg = json.load(f)

limit = cfg.get("max_single_transaction_usd", 5000)
processed = []

for item in items:
    if item["amount"] > limit:
        processed.append({"id": item["id"], "status": "flagged", "requires_manual_review": True})
    else:
        processed.append({"id": item["id"], "status": "approved", "requires_manual_review": False})

with open('/app/output.json', 'w') as f:
    json.dump({"processed": processed, "policy_honored": True}, f, indent=2)
PYEOF

python3 /app/process.py
