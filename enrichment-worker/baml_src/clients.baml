// AI Talent Sourcing - BAML Client Configuration
// Uses Gemini Flash directly via native google-ai provider

client<llm> GeminiFlash {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-2.5-flash"
    api_key env.GEMINI_API_KEY
  }
}

client<llm> GeminiFlashLite {
  provider google-ai
  retry_policy Exponential
  options {
    model "gemini-2.0-flash-lite"
    api_key env.GEMINI_API_KEY
  }
}

// Fallback strategy: try fast model first, then lite
client<llm> ResumeParser {
  provider fallback
  options {
    strategy [GeminiFlash, GeminiFlashLite]
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
