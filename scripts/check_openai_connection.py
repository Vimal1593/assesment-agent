"""Diagnostic script: Note that question generation has migrated to Ollama."""
import os
import sys

print("NOTICE: Question generation has migrated from OpenAI to a locally hosted Ollama Qwen model.")
print("Run 'python scripts/check_ollama_connection.py' to test Ollama connectivity instead.")

key = os.getenv("OPENAI_API_KEY", "")
if not key or key == "replace_with_your_api_key":
    print("OPENAI_API_KEY is not configured (OpenAI is no longer required for question generation).")
    sys.exit(0)
try:
    client = OpenAI(api_key=key, timeout=20, max_retries=0)
    # Minimal Responses API request confirms both API authentication and model access.
    response = client.responses.create(model=model, input="Reply with the single word OK.")
    print(f"SUCCESS: OpenAI API request completed using configured model '{model}'.")
    print(f"Response ID: {response.id}")
except AuthenticationError:
    print("ERROR: Authentication failed. Check the API key in .env; the key was not displayed.")
    sys.exit(3)
except NotFoundError:
    print(f"ERROR: Model '{model}' was not found or is not available to this API project.")
    sys.exit(4)
except RateLimitError:
    print("ERROR: Rate limit or quota issue. Check API billing and limits.")
    sys.exit(5)
except APIConnectionError:
    print("ERROR: Could not connect to the OpenAI API. Check network access and DNS.")
    sys.exit(6)
except Exception as exc:
    print(f"ERROR: API check failed ({type(exc).__name__}). Check server-side diagnostics.")
    sys.exit(7)
