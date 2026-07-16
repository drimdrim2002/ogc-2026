#pragma once

#include "types.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace ogc_native {

enum class ExactVerdict : std::uint8_t { FREE = 0, BLOCKED = 1, ERROR = 2 };

struct ExactQueryResult final {
  ExactVerdict verdict{ExactVerdict::ERROR};
  bool cache_hit{false};
  bool aabb_skipped{false};
  std::int64_t geos_calls{0};
  std::int64_t exact_ns{0};
};

using Wkb = std::vector<std::uint8_t>;

struct ExactShapePayload final {
  std::vector<std::optional<Wkb>> layers;
  std::vector<Wkb> suffix_unions;
};

class ExactGeometryStore final {
 public:
  explicit ExactGeometryStore(
      std::vector<std::vector<ExactShapePayload>> payloads,
      std::size_t cache_cap = std::size_t{1} << 18);
  ~ExactGeometryStore();

  ExactGeometryStore(const ExactGeometryStore&) = delete;
  ExactGeometryStore& operator=(const ExactGeometryStore&) = delete;
  ExactGeometryStore(ExactGeometryStore&&) = delete;
  ExactGeometryStore& operator=(ExactGeometryStore&&) = delete;

  [[nodiscard]] ExactQueryResult is_free(const StaticProblem& problem,
                                         const Row& first,
                                         const Row& second);

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

[[nodiscard]] const char* exact_verdict_name(ExactVerdict verdict) noexcept;

}  // namespace ogc_native
