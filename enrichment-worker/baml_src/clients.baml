// AI Talent Sourcing - BAML Client Configuration
// Uses Gemini Flash via OpenAI-compatible endpoint (pre-configured in env)

client<llm> GeminiFlash {
  provider openai-generic
  retry_policy Exponential
  options {
    model "gemini-2.5-flash"
    api_key env.OPENAI_API_KEY
    base_url env.OPENAI_BASE_URL
  }
}

client<llm> GPT4Mini {
  provider openai-generic
  retry_policy Exponential
  options {
    model "gpt-4.1-mini"
    api_key env.OPENAI_API_KEY
    base_url env.OPENAI_BASE_URL
  }
}

client<llm> GPT4Nano {
  provider openai-generic
  retry_policy Constant
  options {
    model "gpt-4.1-nano"
    api_key env.OPENAI_API_KEY
    base_url env.OPENAI_BASE_URL
  }
}

// Fallback strategy: try fast model first, then more capable
client<llm> ResumeParser {
  provider fallback
  options {
    strategy [GeminiFlash, GPT4Mini]
  }
}

// Retry policies
retry_policy Constant {
  max_retries 3
  strategy {
    type constant_delay
    delay_ms 200
  }
}

retry_policy Exponential {
  max_retries 2
  strategy {
    type exponential_backoff
    delay_ms 300
    multiplier 1.5
    max_delay_ms 10000
  }
}
