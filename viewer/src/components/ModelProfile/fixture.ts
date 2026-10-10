// Test fixture shaped like schema/metrics.schema.json `profiles` (values from fixtures/sample_game).
const pair = (model: string, voters: number, followed: number, ci: [number, number] | null = [0.34, 1.0]) => ({
  heard: voters,
  voters,
  followed,
  vote_follow_rate: voters ? followed / voters : null,
  vote_follow_ci: voters ? ci : null,
  comparisons: voters,
  moved: followed,
  belief_shift_rate: voters ? followed / voters : null,
  belief_shift_ci: null as [number, number] | null,
  baseline_rate: 0.25 as number | null,
  baseline_voters: voters,
  model,
});

const empty = { self_tag: [], jev: [] };

export const metrics = {
  profiles: {
    "gemini-flash": {
      model: "gemini-flash",
      games: 1,
      seats: 1,
      win_rate: 1.0,
      win_ci: [0.21, 1.0],
      wolf_win_rate: 1.0,
      village_win_rate: null,
      openskill: { mu: 27.23, sigma: 8.11, ordinal: 2.91 },
      statements: 4,
      lie_rate: { self_tag: 0.5, jev: null },
      detection: { n: 0, auc: null, brier: null },
      median_latency_ms: 1610.0,
      voice: { voice_id: "cgSgspJ2msm6clMCkdW9", fallback: false },
      fools: { self_tag: [pair("deepseek-v3.1", 6, 5), pair("gpt-oss-120b", 2, 0)], jev: [] },
      fooled_by: empty,
    },
    "llama-4-maverick": {
      model: "llama-4-maverick",
      games: 1,
      seats: 1,
      win_rate: 0.0,
      win_ci: [0.0, 0.79],
      wolf_win_rate: null,
      village_win_rate: 0.0,
      openskill: null,
      statements: 5,
      lie_rate: { self_tag: 0.0, jev: null },
      detection: { n: 11, auc: 1.0, brier: 0.07 },
      median_latency_ms: null,
      voice: { voice_id: "TX3LPaxmHKxFdv7VOQHJ", fallback: true },
      fools: empty,
      fooled_by: { self_tag: [pair("gemini-flash", 2, 1)], jev: [] },
    },
  },
};
