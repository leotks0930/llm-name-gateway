# LLM Name Mapping Gateway

A lightweight, flexible API gateway designed to route, proxy, and manage Large Language Model (LLM) requests seamlessly. 

## 🚀 Features

- **Model Name Mapping & Aliasing:** Easily map arbitrary model names or shorthand aliases to backend provider endpoints.
- **Unified API Interface:** Acts as a drop-in proxy compatible with standard OpenAI-style API schemas.
- **Multi-Provider Support:** Route requests dynamically to different LLM backends (OpenAI, Anthropic, Ollama, OpenRouter, etc.).
- **Lightweight & Fast:** Built for low-overhead deployment in development and production environments.

---

## 🛠️ Getting Started

### Prerequisites

- Node.js / Python / Docker (depending on your core stack)
- Git

### Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/leotks0930/llm-name-gateway.git](https://github.com/leotks0930/llm-name-gateway.git)
   cd llm-name-gateway

```

2. Install dependencies:
```bash
# Example for Node.js
npm install

```


3. Configure your environment variables:
```bash
cp .env.example .env

```


Edit `.env` to supply your API keys and target routing preferences.

---

## ⚙️ Configuration

Configure your model routes and mappings in your configuration file (e.g., `config.json` or via environment variables).

Example mapping structure:

```json
{
  "routes": {
    "gpt-4-alias": "openai/gpt-4o",
    "fast-local": "ollama/llama3"
  }
}

```

---

## 🔌 Usage

Start the gateway server:

```
docker-compose up --build

```

Once running, point your standard OpenAI SDK or client application to your gateway URL instead of the direct provider endpoint:

```
import openai

client = openai.OpenAI(
    base_url="http://localhost:3000/v1",  # Your gateway URL
    api_key="your-gateway-api-key"
)

response = client.chat.completions.create(
    model="fast-local", # Uses your custom gateway alias
    messages=[{"role": "user", "content": "Hello!"}]
)

print(response.choices[0].message.content)

```

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://www.google.com/search?q=../../issues).

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📝 License

Distributed under the [MIT License](https://www.google.com/search?q=LICENSE). See `LICENSE` for more information.
