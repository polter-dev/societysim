// Short display names for the model ids in configs/models.yaml. The catalog's display_name is the
// provider's full id ("Llama-4-Maverick-17B-128E-Instruct-FP8"), too long for seats and panels.

const NAMES: Record<string, string> = {
  "qwen3-235b": "Qwen3 235B",
  "llama4-maverick": "Llama 4 Maverick",
  "gemma4-26b": "Gemma 4 26B",
  "gpt-oss-120b": "GPT-OSS 120B",
  "deepseek-v3.1": "DeepSeek V3.1",
  "gemini-flash-lite": "Gemini Flash Lite",
  "gemini-flash": "Gemini Flash",
  "amd-gpt-oss-20b": "GPT-OSS 20B (AMD)",
  "amd-strix-gpt-oss-20b": "GPT-OSS 20B (Strix)",
  "gpt-5-nano": "GPT-5 nano",
  "gpt-5-mini": "GPT-5 mini",
  human: "Human",
};

/** Short name for a model id ("llama4-maverick" -> "Llama 4 Maverick"); unknown ids come back unchanged. Put the full id in a title. */
export const modelName = (id: string) => NAMES[id] ?? id;
