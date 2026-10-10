// Test fixture shaped like schema/metrics.schema.json DeceptionMatrix.
const mk = (voters: number, followed: number) => ({
  heard: voters,
  voters,
  followed,
  vote_follow_rate: voters ? followed / voters : null,
  vote_follow_ci: voters ? ([0.1, 0.9] as [number, number]) : null,
  comparisons: voters,
  moved: followed,
  belief_shift_rate: voters ? followed / voters : null,
  belief_shift_ci: null as [number, number] | null,
  baseline_rate: 0.25 as number | null,
  baseline_voters: voters,
});

export const fixture = {
  exploratory: true,
  pairs: {
    self_tag: {
      a: { b: mk(10, 8), c: mk(4, 4) },
      b: { a: mk(10, 2), c: mk(6, 3) },
    },
    jev: {},
  },
  baseline: {
    role_claim: { b: { heard: 2, voters: 2, followed: 1, vote_follow_rate: 0.5, vote_follow_ci: [0.09, 0.91] } },
  },
};
