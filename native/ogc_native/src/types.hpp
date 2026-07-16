#pragma once

#include <string_view>
#include <cstdint>
#include <memory>
#include <optional>
#include <utility>
#include <vector>

namespace ogc_native {

class ExactGeometryStore;

struct KernelInfo final {
  std::string_view name;
  int api_version;
};

struct Row final {
  std::int64_t block, bay, orient, x, y, entry, exit;
};

struct Point final { double x, y; };
struct Aabb final { double xmin, ymin, xmax, ymax; };
struct Shape final {
  double xmin, ymin, xmax, ymax;
  std::vector<Point> vertices;
  // These are copied from Python/Shapely preprocessing only for P4's
  // conservative reject-only relation prefilter. `nullopt` preserves an
  // absent/empty layer or suffix exactly; C++ never reconstructs geometry.
  std::vector<std::optional<Aabb>> layer_aabbs;
  std::vector<std::optional<Aabb>> suffix_aabbs;
};

struct Block final {
  std::int64_t release, due, dwell;
  double workload;
  std::vector<double> preferences;
  std::vector<Shape> orientations;
};

struct Bay final { std::int64_t width, height; };

struct StaticProblem final {
  std::vector<Bay> bays;
  std::vector<Block> blocks;
  double w1, w2, w3;
  // Python-normalized WKB is parsed once and owned for this problem's
  // lifetime. Candidate generation never receives Shapely geometry pointers.
  std::shared_ptr<ExactGeometryStore> exact_geometry;
};

// A state is packed once at a Python state-version boundary.  `rows` retains
// Python's block-id placement order for time enumeration; `by_bay` preserves
// IndexedSolutionState._intervals' (entry, exit, block_id) order for every
// anchor and exact-pair traversal.
struct PackedState final {
  std::vector<Row> rows;
  std::vector<std::vector<Row>> by_bay;
  std::vector<double> loads;
  std::int64_t version;
};

struct Prepared final {
  std::vector<Row> rows;
  std::vector<std::int64_t> pair_offsets;
  std::vector<Row> pairs;
  std::vector<double> total, tardiness, assignment;
  std::vector<std::int64_t> fragmentation;
  std::vector<std::int64_t> guide_penalty;
  // One flag per `pairs` entry. It may only be true when both directed
  // layer/suffix AABB scans prove no obstruction is possible.
  std::vector<std::uint8_t> pair_definitely_free;
  std::int64_t state_version, kernel_ns;
  // P7 fix1 streaming metadata.  Full/capped P3 calls use -1 indices; the
  // unbounded production seam prepares exactly one fitting-option/entry
  // chunk so Python can apply its authoritative exact-free stop cadence.
  std::int64_t fitting_option_index{-1}, entry_index{-1};
  std::int64_t fitting_option_count{0}, entry_count{0};
  bool deadline_hit{false};
};

struct Fitting final {
  std::int64_t bay, orient, guide_penalty;
  double preference_penalty;
};

struct ScoredRow final {
  Row row;
  double total, tardiness, assignment;
  std::int64_t fragmentation, guide_penalty;
};

// Phase-1 cursor batches are deliberately small. Definitely-free pairs are
// omitted rather than tagged, so Python only receives relations for which it
// remains the exact authority.
struct CandidateBatch final {
  std::vector<Row> rows;
  std::vector<std::int64_t> pair_offsets;
  std::vector<Row> pairs;
  std::vector<double> total, tardiness, assignment;
  std::vector<std::int64_t> fragmentation, guide_penalty;
  std::int64_t state_version{0};
  std::int64_t kernel_ns{0};
  // Phase-3 run_exact telemetry. These remain zero on the Python-exact
  // next_batch/consume path.
  std::int64_t generated_candidates{0};
  std::int64_t aabb_skipped_pairs{0};
  std::int64_t geos_exact_calls{0};
  std::int64_t first_conflict_skipped_pairs{0};
  std::int64_t exact_cache_hits{0};
  std::int64_t exact_cache_misses{0};
  std::int64_t geos_error_count{0};
  std::int64_t native_exact_ns{0};
  std::int64_t native_cache_ns{0};
  std::int64_t native_error_ns{0};
  bool exact_error{false};
  bool deadline_hit{false};
  bool complete{false};
};

// The cursor owns only compact traversal state. Static problem and packed
// state lifetimes are held by the pybind keep-alive policy.
struct RepairCursor final {
  const StaticProblem* problem{nullptr};
  const PackedState* state{nullptr};
  std::int64_t block_id{0};
  std::optional<Row> current;
  std::int64_t position_cap{0};
  bool prefilter_enabled{false};

  std::vector<Fitting> fitting;
  std::vector<std::int64_t> entries;
  std::int64_t option_index{0};
  std::int64_t entry_index{0};
  std::int64_t position_index{0};
  std::vector<std::pair<std::int64_t, std::int64_t>> positions;

  std::int64_t option_accepted{0};
  std::int64_t accepted_total{0};
  std::vector<ScoredRow> best;

  bool lattice_only{false};
  bool deadline_hit{false};
  bool complete{false};
  bool entry_loaded{false};
  bool pending{false};
  CandidateBatch pending_batch;
  std::int64_t prepare_ns{0};
};

}  // namespace ogc_native
