# process.py
import os
import json
from pathlib import Path

INPUT_DIR = "/opt/ml/processing/input"
OUTPUT_DIR = "/opt/ml/processing/output"

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    records = []

    for root, _, files in os.walk(INPUT_DIR):
        for f in files:
            if f.endswith(".json"):
                with open(os.path.join(root, f), "r") as fh:
                    try:
                        records.append(json.load(fh))
                    except Exception:
                        pass

    out_path = Path(OUTPUT_DIR) / "data.jsonl"
    with out_path.open("w") as out:
        for r in records:
            out.write(json.dumps(r) + "\n")

    print(f"[processing] wrote {len(records)} records to {out_path}")

if __name__ == "__main__":
    main()
