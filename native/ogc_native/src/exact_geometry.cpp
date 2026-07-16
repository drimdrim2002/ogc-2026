#include "exact_geometry.hpp"

#include <geos_c.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <list>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>

namespace ogc_native {
namespace {

struct ExactKey final {
  std::int64_t first_block;
  std::int64_t first_orient;
  std::int64_t second_block;
  std::int64_t second_orient;
  std::int64_t dx;
  std::int64_t dy;

  bool operator==(const ExactKey&) const = default;
};

struct ExactKeyHash final {
  std::size_t operator()(const ExactKey& key) const noexcept {
    std::size_t value = 0xcbf29ce484222325ULL;
    const auto mix = [&](std::int64_t item) {
      value ^= std::hash<std::int64_t>{}(item) + 0x9e3779b97f4a7c15ULL +
               (value << 6) + (value >> 2);
    };
    mix(key.first_block);
    mix(key.first_orient);
    mix(key.second_block);
    mix(key.second_orient);
    mix(key.dx);
    mix(key.dy);
    return value;
  }
};

struct Translation final {
  double dx;
  double dy;
};

int translate_xy(double* x, double* y, void* userdata) {
  const auto* translation = static_cast<const Translation*>(userdata);
  *x += translation->dx;
  *y += translation->dy;
  return std::isfinite(*x) && std::isfinite(*y) ? 1 : 0;
}

bool strict_aabb_overlap(const std::optional<Aabb>& left,
                         const std::optional<Aabb>& right, double dx,
                         double dy) {
  if (!left || !right) return false;
  return left->xmin < right->xmax + dx &&
         right->xmin + dx < left->xmax &&
         left->ymin < right->ymax + dy &&
         right->ymin + dy < left->ymax;
}

}  // namespace

struct ExactGeometryStore::Impl final {
  struct ObstructionResult final {
    ExactVerdict verdict{ExactVerdict::ERROR};
    bool aabb_overlap_seen{false};
    std::int64_t geos_calls{0};
  };

  struct ComputeResult final {
    ExactVerdict verdict{ExactVerdict::ERROR};
    bool aabb_skipped{false};
    std::int64_t geos_calls{0};
  };

  struct CachedVerdict final {
    ExactVerdict verdict{ExactVerdict::ERROR};
    bool aabb_skipped{false};
  };

  struct CacheEntry final {
    CachedVerdict value;
    std::list<ExactKey>::iterator recency;
  };

  struct ExactShape final {
    std::vector<GEOSGeometry*> layers;
    std::vector<GEOSGeometry*> suffix_unions;
    bool valid{true};
  };

  explicit Impl(std::vector<std::vector<ExactShapePayload>> payloads,
                std::size_t requested_cache_cap)
      : context(GEOS_init_r()), cache_cap(requested_cache_cap) {
    if (context == nullptr) return;
    GEOSContext_setNoticeMessageHandler_r(context, &message_handler, this);
    GEOSContext_setErrorMessageHandler_r(context, &message_handler, this);
    auto* reader = GEOSWKBReader_create_r(context);
    if (reader == nullptr) {
      last_error = "GEOSWKBReader_create_r failed";
      return;
    }

    shapes.resize(payloads.size());
    for (std::size_t block = 0; block < payloads.size(); ++block) {
      shapes[block].reserve(payloads[block].size());
      for (const auto& payload : payloads[block]) {
        ExactShape shape;
        shape.layers.reserve(payload.layers.size());
        for (const auto& item : payload.layers) {
          if (!item) {
            shape.layers.push_back(nullptr);
            continue;
          }
          auto* geometry = parse(reader, *item);
          if (geometry == nullptr) shape.valid = false;
          shape.layers.push_back(geometry);
        }
        shape.suffix_unions.reserve(payload.suffix_unions.size());
        for (const auto& item : payload.suffix_unions) {
          auto* geometry = parse(reader, item);
          if (geometry == nullptr) shape.valid = false;
          shape.suffix_unions.push_back(geometry);
        }
        if (shape.layers.size() != shape.suffix_unions.size()) {
          shape.valid = false;
        }
        shapes[block].push_back(std::move(shape));
      }
    }
    GEOSWKBReader_destroy_r(context, reader);
  }

  ~Impl() {
    if (context == nullptr) return;
    for (auto& block : shapes) {
      for (auto& shape : block) {
        for (auto* geometry : shape.layers) {
          if (geometry != nullptr) GEOSGeom_destroy_r(context, geometry);
        }
        for (auto* geometry : shape.suffix_unions) {
          if (geometry != nullptr) GEOSGeom_destroy_r(context, geometry);
        }
      }
    }
    GEOS_finish_r(context);
  }

  static void message_handler(const char* message, void* userdata) {
    auto* self = static_cast<Impl*>(userdata);
    self->last_error = message == nullptr ? "GEOS error" : message;
  }

  GEOSGeometry* parse(GEOSWKBReader* reader, const Wkb& bytes) {
    if (bytes.empty()) {
      last_error = "empty WKB";
      return nullptr;
    }
    return GEOSWKBReader_read_r(context, reader, bytes.data(), bytes.size());
  }

  ObstructionResult obstruction(const StaticProblem& problem, const Row& mover,
                                const Row& stationary) {
    ObstructionResult result;
    if (context == nullptr) return result;
    try {
      const auto& mover_shape = shapes.at(mover.block).at(mover.orient);
      const auto& stationary_shape =
          shapes.at(stationary.block).at(stationary.orient);
      const auto& mover_bounds =
          problem.blocks.at(mover.block).orientations.at(mover.orient);
      const auto& stationary_bounds =
          problem.blocks.at(stationary.block).orientations.at(stationary.orient);
      if (!mover_shape.valid || !stationary_shape.valid) {
        return result;
      }
      const auto limit = std::min(
          {mover_shape.layers.size(), stationary_shape.suffix_unions.size(),
           mover_bounds.layer_aabbs.size(),
           stationary_bounds.suffix_aabbs.size()});
      const double dx = static_cast<double>(stationary.x - mover.x);
      const double dy = static_cast<double>(stationary.y - mover.y);
      for (std::size_t layer = 0; layer < limit; ++layer) {
        auto* mover_geometry = mover_shape.layers[layer];
        auto* stationary_geometry = stationary_shape.suffix_unions[layer];
        if (mover_geometry == nullptr || stationary_geometry == nullptr) {
          if (mover_geometry == nullptr) continue;
          return result;
        }
        if (!strict_aabb_overlap(mover_bounds.layer_aabbs[layer],
                                 stationary_bounds.suffix_aabbs[layer], dx,
                                 dy)) {
          continue;
        }
        result.aabb_overlap_seen = true;

        GEOSGeometry* translated = nullptr;
        const GEOSGeometry* intersection_right = stationary_geometry;
        if (dx != 0.0 || dy != 0.0) {
          Translation translation{dx, dy};
          translated = GEOSGeom_transformXY_r(
              context, stationary_geometry, &translate_xy, &translation);
          if (translated == nullptr) return result;
          intersection_right = translated;
        }
        ++result.geos_calls;
        auto* intersection = GEOSIntersection_r(
            context, mover_geometry, intersection_right);
        if (translated != nullptr) GEOSGeom_destroy_r(context, translated);
        if (intersection == nullptr) return result;

        const char empty = GEOSisEmpty_r(context, intersection);
        if (empty != 0 && empty != 1) {
          GEOSGeom_destroy_r(context, intersection);
          return result;
        }
        if (empty == 0) {
          double area = 0.0;
          const int area_ok = GEOSArea_r(context, intersection, &area);
          GEOSGeom_destroy_r(context, intersection);
          if (area_ok == 0 || !std::isfinite(area)) {
            return result;
          }
          if (area > 0.0) {
            result.verdict = ExactVerdict::BLOCKED;
            return result;
          }
        } else {
          GEOSGeom_destroy_r(context, intersection);
        }
      }
      result.verdict = ExactVerdict::FREE;
      return result;
    } catch (...) {
      return result;
    }
  }

  ComputeResult compute(const StaticProblem& problem, const Row& first,
                        const Row& second) {
    const auto forward = obstruction(problem, first, second);
    if (forward.verdict == ExactVerdict::ERROR) {
      return {ExactVerdict::ERROR, false, forward.geos_calls};
    }
    if (forward.verdict == ExactVerdict::BLOCKED) {
      return {ExactVerdict::BLOCKED, false, forward.geos_calls};
    }
    const auto reverse = obstruction(problem, second, first);
    const auto geos_calls = forward.geos_calls + reverse.geos_calls;
    if (reverse.verdict == ExactVerdict::ERROR) {
      return {ExactVerdict::ERROR, false, geos_calls};
    }
    return {
        reverse.verdict == ExactVerdict::BLOCKED ? ExactVerdict::BLOCKED
                                                  : ExactVerdict::FREE,
        !forward.aabb_overlap_seen && !reverse.aabb_overlap_seen,
        geos_calls,
    };
  }

  GEOSContextHandle_t context{nullptr};
  std::vector<std::vector<ExactShape>> shapes;
  std::list<ExactKey> cache_recency;
  std::unordered_map<ExactKey, CacheEntry, ExactKeyHash> cache;
  std::size_t cache_cap;
  std::string last_error;
};

ExactGeometryStore::ExactGeometryStore(
    std::vector<std::vector<ExactShapePayload>> payloads,
    std::size_t cache_cap)
    : impl_(std::make_unique<Impl>(std::move(payloads), cache_cap)) {}

ExactGeometryStore::~ExactGeometryStore() = default;

ExactQueryResult ExactGeometryStore::is_free(const StaticProblem& problem,
                                             const Row& left,
                                             const Row& right) {
  const auto started = std::chrono::steady_clock::now();
  const auto finish = [&](ExactVerdict verdict, bool cache_hit,
                          bool aabb_skipped, std::int64_t geos_calls) {
    return ExactQueryResult{
        verdict,
        cache_hit,
        aabb_skipped,
        geos_calls,
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - started)
            .count(),
    };
  };
  if (left.block == right.block || left.bay != right.bay) {
    return finish(left.block == right.block ? ExactVerdict::ERROR
                                            : ExactVerdict::FREE,
                  false, left.bay != right.bay, 0);
  }
  const Row* first = &left;
  const Row* second = &right;
  if (first->block > second->block) std::swap(first, second);
  const ExactKey key{
      first->block,
      first->orient,
      second->block,
      second->orient,
      second->x - first->x,
      second->y - first->y,
  };
  if (const auto found = impl_->cache.find(key); found != impl_->cache.end()) {
    impl_->cache_recency.splice(impl_->cache_recency.end(),
                                impl_->cache_recency,
                                found->second.recency);
    return finish(found->second.value.verdict, true,
                  found->second.value.aabb_skipped, 0);
  }
  const auto computed = impl_->compute(problem, *first, *second);
  if (computed.verdict != ExactVerdict::ERROR && impl_->cache_cap != 0) {
    if (impl_->cache.size() >= impl_->cache_cap) {
      impl_->cache.erase(impl_->cache_recency.front());
      impl_->cache_recency.pop_front();
    }
    const auto recency = impl_->cache_recency.insert(
        impl_->cache_recency.end(), key);
    impl_->cache.emplace(key, Impl::CacheEntry{
                                  Impl::CachedVerdict{computed.verdict,
                                                      computed.aabb_skipped},
                                  recency});
  }
  return finish(computed.verdict, false, computed.aabb_skipped,
                computed.geos_calls);
}

const char* exact_verdict_name(ExactVerdict verdict) noexcept {
  switch (verdict) {
    case ExactVerdict::FREE:
      return "FREE";
    case ExactVerdict::BLOCKED:
      return "BLOCKED";
    case ExactVerdict::ERROR:
      return "ERROR";
  }
  return "ERROR";
}

}  // namespace ogc_native
