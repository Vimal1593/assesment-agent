"""Safe Ollama connectivity check: reports model, status, and availability."""
import os
import sys
import httpx

base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

print(f"Connecting to Ollama at: {base_url} ...")
try:
    with httpx.Client(timeout=10.0) as client:
        # Check server health / tags
        resp = client.get(f"{base_url}/api/tags")
        if resp.status_code != 200:
            print(f"ERROR: Ollama server returned status {resp.status_code}: {resp.text}")
            sys.exit(2)

        tags_data = resp.json()
        models = [m.get("name") for m in tags_data.get("models", [])]
        print(f"SUCCESS: Ollama server is running. Available models: {', '.join(models) if models else 'None'}")

        # Check if the configured model is installed
        model_installed = any(m == model or m.startswith(f"{model}:") or model.startswith(f"{m}:") for m in models)
        if not model_installed:
            print(f"WARNING: Configured model '{model}' is not in the installed models list.")
            print(f"To pull it, run: docker compose exec ollama ollama run {model}")
        else:
            print(f"SUCCESS: Model '{model}' is ready.")

        # Test prompt
        print(f"Testing generation using model '{model}'...")
        gen_resp = client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                "stream": False,
            },
            timeout=30.0,
        )
        if gen_resp.status_code == 200:
            content = gen_resp.json().get("message", {}).get("content", "").strip()
            print(f"SUCCESS: Ollama generation completed. Response: '{content}'")
        else:
            print(f"ERROR: Generation test failed with status {gen_resp.status_code}: {gen_resp.text}")
            sys.exit(3)

except httpx.ConnectError:
    print(f"ERROR: Could not connect to Ollama at '{base_url}'. Check if the Ollama service or container is running.")
    sys.exit(4)
except httpx.TimeoutException:
    print(f"ERROR: Connection or generation timed out contacting '{base_url}'.")
    sys.exit(5)
except Exception as exc:
    print(f"ERROR: Unexpected failure ({type(exc).__name__}): {exc}")
    sys.exit(6)
