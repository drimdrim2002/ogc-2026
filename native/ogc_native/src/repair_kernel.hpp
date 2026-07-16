#pragma once

#include "types.hpp"

namespace ogc_native {

// P2 intentionally exposes only a deterministic, side-effect-free scaffold.
// Candidate enumeration, scoring, relation filtering, and threads begin no
// earlier than their separately approved phases.
[[nodiscard]] KernelInfo empty_kernel_info() noexcept;
[[nodiscard]] Prepared prepare_candidates(const StaticProblem& problem,
                                          const PackedState& state,
                                          std::int64_t block_id,
                                          const Row* current,
                                          std::int64_t time_cap,
                                          std::int64_t anchor_cap,
                                          std::int64_t attempt_cap,
                                          bool prefilter_enabled);
[[nodiscard]] Prepared prepare_candidate_chunk(const StaticProblem& problem,
                                               const PackedState& state,
                                               std::int64_t block_id,
                                               const Row* current,
                                               std::int64_t time_cap,
                                               std::int64_t anchor_cap,
                                               bool prefilter_enabled,
                                               std::int64_t fitting_option_index,
                                               std::int64_t entry_index,
                                               bool lattice_only,
                                               double remaining_seconds);

[[nodiscard]] RepairCursor make_repair_cursor(const StaticProblem& problem,
                                              const PackedState& state,
                                              std::int64_t block_id,
                                              const Row* current,
                                              std::int64_t time_cap,
                                              std::int64_t position_cap,
                                              bool prefilter_enabled,
                                              bool lattice_only);
[[nodiscard]] CandidateBatch next_batch(RepairCursor& cursor,
                                        std::int64_t max_rows,
                                        double remaining_seconds);
[[nodiscard]] CandidateBatch run_exact(RepairCursor& cursor,
                                       double remaining_seconds);
void consume(RepairCursor& cursor,
             const std::vector<std::uint8_t>& verdicts,
             std::int64_t processed_rows);
[[nodiscard]] CandidateBatch finalize_top3(const RepairCursor& cursor);

}  // namespace ogc_native
