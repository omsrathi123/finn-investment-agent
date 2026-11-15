from llm import ask_json, FAST

if __name__ == "__main__":
    print(ask_json('Return JSON like {"ok": true, "word": "hello"}', FAST))