from openai import OpenAI
import os

def _load_env():
    fp = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(fp):
        return
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    if k in {"OPENROUTER_API_KEY", "OPENROUTER_MODEL"}:
                        os.environ[k] = v
                    else:
                        os.environ.setdefault(k, v)
    except Exception:
        pass

_load_env()

class LLMClient:
    def __init__(self, model=None):
        self.api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        m = model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
        self.model = m
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key
        ) if self.api_key else None

    def chat(self, system, user, temperature=0.2):
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=64000
            )
            return r.choices[0].message.content
        except Exception as e:
            print(f"Error in LLMClient.chat: {e}")
            return ""