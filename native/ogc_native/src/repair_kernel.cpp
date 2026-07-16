#include "repair_kernel.hpp"

#include "exact_geometry.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <tuple>
#include <unordered_set>

namespace ogc_native {

KernelInfo empty_kernel_info() noexcept {
  return {.name = "ogc_native_empty_kernel", .api_version = 1};
}

namespace {
bool overlaps(const Row& a, const Row& b) { return a.entry < b.exit && b.entry < a.exit; }
bool aabb_overlap(const std::optional<Aabb>& left, const std::optional<Aabb>& right,
                  double dx, double dy) {
  if (!left || !right) return false;
  return left->xmin < right->xmax + dx && right->xmin + dx < left->xmax &&
         left->ymin < right->ymax + dy && right->ymin + dy < left->ymax;
}
bool obstruction_possible(const StaticProblem& problem, const Row& mover,
                          const Row& stationary) {
  const auto& mover_shape = problem.blocks.at(mover.block).orientations.at(mover.orient);
  const auto& stationary_shape = problem.blocks.at(stationary.block).orientations.at(stationary.orient);
  const auto limit = std::min(mover_shape.layer_aabbs.size(), stationary_shape.suffix_aabbs.size());
  const double dx = static_cast<double>(stationary.x - mover.x);
  const double dy = static_cast<double>(stationary.y - mover.y);
  for (std::size_t index = 0; index < limit; ++index) {
    if (aabb_overlap(mover_shape.layer_aabbs[index], stationary_shape.suffix_aabbs[index], dx, dy)) return true;
  }
  return false;
}
bool definitely_free(const StaticProblem& problem, const Row& left, const Row& right) {
  // This must stay strictly weaker than an exact relation: any AABB overlap
  // in either direction becomes UNKNOWN and is resolved by Python/Shapely.
  return !obstruction_possible(problem, left, right) && !obstruction_possible(problem, right, left);
}
std::vector<std::int64_t> times(const Block& block, const std::vector<Row>& retained,
                                const Row* current, std::int64_t cap) {
  std::vector<std::int64_t> values{block.release};
  for (const auto& row : retained) { values.push_back(row.exit); values.push_back(row.entry - block.dwell); }
  if (current) { values.push_back(current->entry - 1); values.push_back(current->entry); values.push_back(current->entry + 1); }
  values.erase(std::remove_if(values.begin(), values.end(), [&](auto v) { return v < block.release; }), values.end());
  std::sort(values.begin(), values.end()); values.erase(std::unique(values.begin(), values.end()), values.end());
  const auto guide = current ? current->entry : 0;
  std::stable_sort(values.begin(), values.end(), [&](auto a, auto b) {
    auto ka = std::tuple{std::max<std::int64_t>(0, a + block.dwell - block.due), current ? std::llabs(a - guide) : 0LL, a};
    auto kb = std::tuple{std::max<std::int64_t>(0, b + block.dwell - block.due), current ? std::llabs(b - guide) : 0LL, b};
    return ka < kb;
  });
  if (cap >= 0 && static_cast<std::size_t>(cap) < values.size()) values.resize(cap);
  return values;
}
void add_rounding(std::vector<std::pair<std::int64_t,std::int64_t>>& out, double x, double y, std::size_t raw_cap) {
  std::vector<std::int64_t> xs{static_cast<std::int64_t>(std::floor(x)), static_cast<std::int64_t>(std::ceil(x))};
  std::vector<std::int64_t> ys{static_cast<std::int64_t>(std::floor(y)), static_cast<std::int64_t>(std::ceil(y))};
  std::sort(xs.begin(), xs.end()); xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
  std::sort(ys.begin(), ys.end()); ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
  for (auto ix : xs)
    for (auto iy : ys) {
      if (out.size() >= raw_cap) return; out.emplace_back(ix, iy);
    }
}
double vdc(std::int64_t index, int base) { double value=0, denom=1; while(index) { auto rem=index%base; index/=base; denom*=base; value += rem/denom; } return value; }
std::vector<std::pair<std::int64_t,std::int64_t>> lattice_points(
    std::int64_t xl, std::int64_t xu, std::int64_t yl, std::int64_t yu,
    std::int64_t cap) {
  if (cap <= 0) return {};
  const auto width=xu-xl+1, height=yu-yl+1, total=width*height;
  std::vector<std::pair<std::int64_t,std::int64_t>> points;
  if (total <= cap) {
    for (auto y=yl; y<=yu; ++y)
      for (auto x=xl; x<=xu; ++x)
        points.emplace_back(x,y);
    return points;
  }
  std::unordered_set<std::string> seen;
  for (std::int64_t i=1; static_cast<std::int64_t>(points.size())<std::min(cap,total); ++i) {
    auto point=std::make_pair(
        xl+std::min<std::int64_t>(width-1, static_cast<std::int64_t>(vdc(i,2)*width)),
        yl+std::min<std::int64_t>(height-1, static_cast<std::int64_t>(vdc(i,3)*height)));
    auto key=std::to_string(point.first)+":"+std::to_string(point.second);
    if (seen.insert(key).second) points.push_back(point);
  }
  return points;
}
std::vector<std::pair<std::int64_t,std::int64_t>> positions(const Shape& candidate, const Bay& bay,
    const std::vector<Row>& active, const StaticProblem& problem,
    std::int64_t entry, std::int64_t exit, const Row* current, std::int64_t anchor_cap) {
  auto xl=static_cast<std::int64_t>(std::ceil(-candidate.xmin)), xu=static_cast<std::int64_t>(std::floor(bay.width-candidate.xmax));
  auto yl=static_cast<std::int64_t>(std::ceil(-candidate.ymin)), yu=static_cast<std::int64_t>(std::floor(bay.height-candidate.ymax));
  if (xl>xu || yl>yu || anchor_cap<=0) return {};
  std::vector<std::pair<std::int64_t,std::int64_t>> raw; const auto raw_cap=static_cast<std::size_t>(anchor_cap*8);
  if (current) raw.emplace_back(current->x,current->y);
  raw.insert(raw.end(), {{xl,yl},{xu,yl},{xl,yu},{xu,yu}});
  for (const auto& existing : active) {
    if (!(existing.entry < exit && entry < existing.exit)) continue;
    const auto& shape=problem.blocks.at(existing.block).orientations.at(existing.orient);
    const double ex0=shape.xmin+existing.x, ey0=shape.ymin+existing.y, ex1=shape.xmax+existing.x, ey1=shape.ymax+existing.y;
    for (double x : {ex0-candidate.xmax, ex1-candidate.xmin}) for (double y : {static_cast<double>(yl),static_cast<double>(yu),std::floor(ey0-candidate.ymin)}) add_rounding(raw,x,y,raw_cap);
    for (double y : {ey0-candidate.ymax, ey1-candidate.ymin}) for (double x : {static_cast<double>(xl),static_cast<double>(xu),std::floor(ex0-candidate.xmin)}) add_rounding(raw,x,y,raw_cap);
    const auto candidate_limit=std::min<std::size_t>(32, candidate.vertices.size());
    const auto existing_limit=std::min<std::size_t>(32, shape.vertices.size());
    for (std::size_t ci=0; ci<candidate_limit; ++ci) { for (std::size_t ei=0; ei<existing_limit; ++ei) { const auto& cv=candidate.vertices[ci]; const auto& ev=shape.vertices[ei]; add_rounding(raw,ev.x+existing.x-cv.x,ev.y+existing.y-cv.y,raw_cap); if(raw.size()>=raw_cap) break; } if(raw.size()>=raw_cap) break; }
    if(raw.size()>=raw_cap) break;
  }
  const auto lattice=lattice_points(xl,xu,yl,yu,anchor_cap);
  raw.insert(raw.end(), lattice.begin(), lattice.end());
  std::vector<std::pair<std::int64_t,std::int64_t>> output; std::unordered_set<std::string> seen;
  for(auto point:raw) { if(point.first<xl||point.first>xu||point.second<yl||point.second>yu) continue; auto key=std::to_string(point.first)+":"+std::to_string(point.second); if(seen.insert(key).second) { output.push_back(point); if(static_cast<std::int64_t>(output.size())>=anchor_cap) break; } }
  return output;
}
std::int64_t fragments(const std::vector<Row>& retained, const Row& candidate) {
  std::vector<Row> rows; for(const auto& row:retained) if(row.bay==candidate.bay) rows.push_back(row); rows.push_back(candidate);
  std::sort(rows.begin(),rows.end(),[](const Row&a,const Row&b){return std::tie(a.entry,a.exit)<std::tie(b.entry,b.exit);}); std::int64_t components=0,right=0; bool first=true;
  for(const auto& row:rows) { if(first||row.entry>right){++components;right=row.exit;first=false;} else right=std::max(right,row.exit); } return std::max<std::int64_t>(0,components-1);
}
double assignment(const StaticProblem& p,const std::vector<double>& loads,std::int64_t block,std::int64_t bay) {
  double avg=0; for(const auto& b:p.bays) avg+=static_cast<double>(b.width*b.height); avg/=p.bays.size();
  auto range=[&](bool extra){ double lo=std::numeric_limits<double>::infinity(),hi=-lo; for(std::size_t i=0;i<p.bays.size();++i){double value=loads[i]+(extra&&static_cast<std::int64_t>(i)==bay?p.blocks[block].workload:0); value*=avg/(p.bays[i].width*p.bays[i].height);lo=std::min(lo,value);hi=std::max(hi,value);} return std::floor(hi-lo);};
  auto& prefs=p.blocks[block].preferences; return p.w2*(range(true)-range(false))+p.w3*(*std::max_element(prefs.begin(),prefs.end())-prefs.at(bay));
}

std::vector<Fitting> fitting_options(const StaticProblem& p, const Block& block,
                                     const Row* current) {
  std::vector<Fitting> fitting;
  for (std::size_t bay = 0; bay < p.bays.size(); ++bay) {
    for (std::size_t orient = 0; orient < block.orientations.size(); ++orient) {
      const auto& shape = block.orientations[orient];
      const auto xl = std::ceil(-shape.xmin);
      const auto xu = std::floor(p.bays[bay].width - shape.xmax);
      const auto yl = std::ceil(-shape.ymin);
      const auto yu = std::floor(p.bays[bay].height - shape.ymax);
      if (xl <= xu && yl <= yu) {
        fitting.push_back({
            static_cast<std::int64_t>(bay), static_cast<std::int64_t>(orient),
            current && static_cast<std::int64_t>(bay) != current->bay ? 1 : 0,
            *std::max_element(block.preferences.begin(), block.preferences.end()) -
                block.preferences[bay],
        });
      }
    }
  }
  std::sort(fitting.begin(), fitting.end(), [](const Fitting& a, const Fitting& b) {
    return std::tie(a.guide_penalty, a.preference_penalty, a.bay, a.orient) <
           std::tie(b.guide_penalty, b.preference_penalty, b.bay, b.orient);
  });
  return fitting;
}

std::vector<std::pair<std::int64_t, std::int64_t>> row_grid_positions(
    const Shape& shape, const Bay& bay, std::int64_t cap) {
  std::vector<std::pair<std::int64_t, std::int64_t>> output;
  if (cap <= 0) return output;
  const auto xl = static_cast<std::int64_t>(std::ceil(-shape.xmin));
  const auto xu = static_cast<std::int64_t>(std::floor(bay.width - shape.xmax));
  const auto yl = static_cast<std::int64_t>(std::ceil(-shape.ymin));
  const auto yu = static_cast<std::int64_t>(std::floor(bay.height - shape.ymax));
  for (auto y = yl; y <= yu; ++y) {
    for (auto x = xl; x <= xu; ++x) {
      output.emplace_back(x, y);
      if (static_cast<std::int64_t>(output.size()) >= cap) return output;
    }
  }
  return output;
}

void append_candidate(Prepared& out, const StaticProblem& p, const PackedState& state,
                      std::int64_t block_id, const Fitting& option,
                      std::int64_t entry, std::int64_t x, std::int64_t y,
                      bool prefilter_enabled) {
  const auto& block = p.blocks[block_id];
  Row row{block_id, option.bay, option.orient, x, y, entry, entry + block.dwell};
  out.rows.push_back(row);
  for (const auto& old : state.by_bay.at(option.bay)) {
    if (overlaps(row, old)) {
      if (prefilter_enabled && definitely_free(p, row, old)) continue;
      out.pairs.push_back(old);
      out.pair_definitely_free.push_back(false);
    }
  }
  out.pair_offsets.push_back(out.pairs.size());
  const auto tardiness = static_cast<double>(
      std::max<std::int64_t>(0, row.exit - block.due));
  const auto assignment_delta = assignment(p, state.loads, block_id, option.bay);
  out.tardiness.push_back(tardiness);
  out.assignment.push_back(assignment_delta);
  out.total.push_back(p.w1 * tardiness + assignment_delta);
  out.fragmentation.push_back(fragments(state.rows, row));
  out.guide_penalty.push_back(option.guide_penalty);
}

ScoredRow scored_candidate(const StaticProblem& p, const PackedState& state,
                           std::int64_t block_id, const Fitting& option,
                           std::int64_t entry, std::int64_t x,
                           std::int64_t y) {
  const auto& block = p.blocks.at(block_id);
  Row row{block_id, option.bay, option.orient, x, y, entry,
          entry + block.dwell};
  const auto tardiness = static_cast<double>(
      std::max<std::int64_t>(0, row.exit - block.due));
  const auto assignment_delta = assignment(p, state.loads, block_id, option.bay);
  return {
      row,
      p.w1 * tardiness + assignment_delta,
      tardiness,
      assignment_delta,
      fragments(state.rows, row),
      option.guide_penalty,
  };
}

void append_candidate(CandidateBatch& out, const StaticProblem& p,
                      const PackedState& state, std::int64_t block_id,
                      const Fitting& option, std::int64_t entry,
                      std::int64_t x, std::int64_t y,
                      bool prefilter_enabled) {
  const auto scored = scored_candidate(p, state, block_id, option, entry, x, y);
  out.rows.push_back(scored.row);
  for (const auto& old : state.by_bay.at(option.bay)) {
    if (!overlaps(scored.row, old)) continue;
    if (prefilter_enabled && definitely_free(p, scored.row, old)) continue;
    out.pairs.push_back(old);
  }
  out.pair_offsets.push_back(static_cast<std::int64_t>(out.pairs.size()));
  out.total.push_back(scored.total);
  out.tardiness.push_back(scored.tardiness);
  out.assignment.push_back(scored.assignment);
  out.fragmentation.push_back(scored.fragmentation);
  out.guide_penalty.push_back(scored.guide_penalty);
}

bool score_less(const ScoredRow& left, const ScoredRow& right) {
  const auto& a = left.row;
  const auto& b = right.row;
  return std::tie(left.total, left.fragmentation, left.guide_penalty,
                  a.exit, a.entry, a.bay, a.orient, a.x, a.y, a.block) <
         std::tie(right.total, right.fragmentation, right.guide_penalty,
                  b.exit, b.entry, b.bay, b.orient, b.x, b.y, b.block);
}

void retain_top3(RepairCursor& cursor, ScoredRow scored) {
  cursor.best.push_back(std::move(scored));
  std::sort(cursor.best.begin(), cursor.best.end(), score_less);
  if (cursor.best.size() > 3) cursor.best.resize(3);
}

void validate_request(const StaticProblem& p, const PackedState& state,
                      std::int64_t block_id) {
  if (block_id < 0 || static_cast<std::size_t>(block_id) >= p.blocks.size())
    throw std::invalid_argument("invalid block_id");
  if (state.loads.size() != p.bays.size() || state.by_bay.size() != p.bays.size())
    throw std::invalid_argument("packed state shape");
}
}  // namespace

Prepared prepare_candidates(const StaticProblem& p,const PackedState& state,std::int64_t block_id,const Row* current,std::int64_t time_cap,std::int64_t anchor_cap,std::int64_t attempt_cap,bool prefilter_enabled) {
  const auto started=std::chrono::steady_clock::now();
  validate_request(p, state, block_id);
  Prepared out; out.state_version=state.version; out.pair_offsets.push_back(0); const auto finish=[&](){out.kernel_ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-started).count();return out;}; const auto& block=p.blocks[block_id]; auto entries=times(block,state.rows,current,time_cap); std::int64_t attempted=0;
  auto fitting = fitting_options(p, block, current);
  out.fitting_option_count = static_cast<std::int64_t>(fitting.size());
  out.entry_count = static_cast<std::int64_t>(entries.size());
  for(const auto& option:fitting){const auto bay=option.bay,orient=option.orient; for(auto entry:entries){const Row* cp=(current&&current->bay==bay&&current->orient==orient)?current:nullptr; const auto& active=state.by_bay.at(bay); for(auto [x,y]:positions(block.orientations[orient],p.bays[bay],active,p,entry,entry+block.dwell,cp,anchor_cap)){if(attempt_cap>=0&&attempted>=attempt_cap)return finish();++attempted;append_candidate(out,p,state,block_id,option,entry,x,y,prefilter_enabled);}}}
  return finish();
}

Prepared prepare_candidate_chunk(const StaticProblem& p, const PackedState& state,
                                 std::int64_t block_id, const Row* current,
                                 std::int64_t time_cap, std::int64_t anchor_cap,
                                 bool prefilter_enabled,
                                 std::int64_t fitting_option_index,
                                 std::int64_t entry_index, bool lattice_only,
                                 double remaining_seconds) {
  const auto started = std::chrono::steady_clock::now();
  validate_request(p, state, block_id);
  if (!std::isfinite(remaining_seconds) || remaining_seconds < 0.0)
    throw std::invalid_argument("invalid remaining_seconds");
  Prepared out;
  out.state_version = state.version;
  out.fitting_option_index = fitting_option_index;
  out.entry_index = entry_index;
  out.pair_offsets.push_back(0);
  const auto finish = [&]() {
    out.kernel_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - started)
                        .count();
    return out;
  };
  const auto& block = p.blocks[block_id];
  const auto fitting = fitting_options(p, block, current);
  const auto entries = times(block, state.rows, current, time_cap);
  out.fitting_option_count = static_cast<std::int64_t>(fitting.size());
  out.entry_count = static_cast<std::int64_t>(entries.size());
  if (out.fitting_option_count == 0 || out.entry_count == 0) return finish();
  if (fitting_option_index < 0 || fitting_option_index >= out.fitting_option_count ||
      entry_index < 0 || entry_index >= out.entry_count)
    throw std::invalid_argument("invalid candidate chunk cursor");
  const auto& option = fitting[static_cast<std::size_t>(fitting_option_index)];
  const auto entry = entries[static_cast<std::size_t>(entry_index)];
  const Row* current_position =
      current && current->bay == option.bay && current->orient == option.orient
          ? current
          : nullptr;
  const auto& shape = block.orientations[option.orient];
  const auto& bay = p.bays[option.bay];
  const auto& active = state.by_bay.at(option.bay);
  const auto candidates = lattice_only
                              ? row_grid_positions(shape, bay, anchor_cap)
                              : positions(shape, bay, active, p, entry,
                                          entry + block.dwell, current_position,
                                          anchor_cap);
  const auto deadline = started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                                      std::chrono::duration<double>(remaining_seconds));
  for (std::size_t index = 0; index < candidates.size(); ++index) {
    if (index % 16 == 0 && std::chrono::steady_clock::now() >= deadline) {
      out.deadline_hit = true;
      break;
    }
    append_candidate(out, p, state, block_id, option, entry,
                     candidates[index].first, candidates[index].second,
                     prefilter_enabled);
  }
  return finish();
}

RepairCursor make_repair_cursor(const StaticProblem& p,
                                const PackedState& state,
                                std::int64_t block_id, const Row* current,
                                std::int64_t time_cap,
                                std::int64_t position_cap,
                                bool prefilter_enabled,
                                bool lattice_only) {
  const auto started = std::chrono::steady_clock::now();
  validate_request(p, state, block_id);
  if (time_cap < 0 || position_cap < 0)
    throw std::invalid_argument("cursor caps must be nonnegative");
  RepairCursor cursor;
  cursor.problem = &p;
  cursor.state = &state;
  cursor.block_id = block_id;
  if (current) cursor.current = *current;
  cursor.position_cap = position_cap;
  cursor.prefilter_enabled = prefilter_enabled;
  cursor.lattice_only = lattice_only;
  const auto& block = p.blocks.at(block_id);
  cursor.fitting = fitting_options(p, block, current);
  cursor.entries = times(block, state.rows, current, time_cap);
  cursor.complete = cursor.fitting.empty() || cursor.entries.empty();
  cursor.prepare_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                          std::chrono::steady_clock::now() - started)
                          .count();
  return cursor;
}

namespace {
void finish_cursor_entry(RepairCursor& cursor) {
  cursor.entry_loaded = false;
  cursor.positions.clear();
  cursor.position_index = 0;
  ++cursor.entry_index;
  if (cursor.option_accepted >= 3 ||
      cursor.entry_index >= static_cast<std::int64_t>(cursor.entries.size())) {
    ++cursor.option_index;
    cursor.entry_index = 0;
    cursor.option_accepted = 0;
    if (!cursor.current && cursor.accepted_total >= 3) cursor.complete = true;
  }
  if (cursor.option_index >= static_cast<std::int64_t>(cursor.fitting.size()))
    cursor.complete = true;
}

void load_cursor_entry(RepairCursor& cursor) {
  const auto& p = *cursor.problem;
  const auto& state = *cursor.state;
  const auto& block = p.blocks.at(cursor.block_id);
  const auto& option = cursor.fitting.at(
      static_cast<std::size_t>(cursor.option_index));
  const auto entry = cursor.entries.at(
      static_cast<std::size_t>(cursor.entry_index));
  const Row* current_position =
      cursor.current && cursor.current->bay == option.bay &&
              cursor.current->orient == option.orient
          ? &*cursor.current
          : nullptr;
  const auto& shape = block.orientations.at(option.orient);
  const auto& bay = p.bays.at(option.bay);
  cursor.positions = cursor.lattice_only
                         ? row_grid_positions(shape, bay, cursor.position_cap)
                         : positions(shape, bay, state.by_bay.at(option.bay), p,
                                     entry, entry + block.dwell,
                                     current_position, cursor.position_cap);
  cursor.position_index = 0;
  cursor.entry_loaded = true;
}
}  // namespace

CandidateBatch next_batch(RepairCursor& cursor, std::int64_t max_rows,
                          double remaining_seconds) {
  const auto started = std::chrono::steady_clock::now();
  if (cursor.problem == nullptr || cursor.state == nullptr)
    throw std::invalid_argument("uninitialized repair cursor");
  if (cursor.pending)
    throw std::logic_error("consume the pending candidate batch first");
  if (max_rows <= 0 || max_rows > 16)
    throw std::invalid_argument("max_rows must be between 1 and 16");
  if (!std::isfinite(remaining_seconds) || remaining_seconds < 0.0)
    throw std::invalid_argument("invalid remaining_seconds");

  CandidateBatch out;
  out.state_version = cursor.state->version;
  out.pair_offsets.push_back(0);
  const auto deadline =
      started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(remaining_seconds));
  const auto finish = [&]() {
    out.deadline_hit = cursor.deadline_hit;
    out.complete = cursor.complete;
    out.kernel_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - started)
                        .count();
    if (!out.rows.empty()) {
      cursor.pending_batch = out;
      cursor.pending = true;
    }
    return out;
  };

  if (cursor.complete || cursor.deadline_hit) return finish();
  while (out.rows.empty() && !cursor.complete && !cursor.deadline_hit) {
    if (std::chrono::steady_clock::now() >= deadline) {
      cursor.deadline_hit = true;
      break;
    }
    if (!cursor.entry_loaded) {
      load_cursor_entry(cursor);
      if (std::chrono::steady_clock::now() >= deadline) {
        cursor.deadline_hit = true;
        break;
      }
    }
    if (cursor.position_index >=
        static_cast<std::int64_t>(cursor.positions.size())) {
      finish_cursor_entry(cursor);
      continue;
    }

    const auto& option = cursor.fitting.at(
        static_cast<std::size_t>(cursor.option_index));
    const auto entry = cursor.entries.at(
        static_cast<std::size_t>(cursor.entry_index));
    while (static_cast<std::int64_t>(out.rows.size()) < max_rows &&
           cursor.position_index <
               static_cast<std::int64_t>(cursor.positions.size())) {
      if (std::chrono::steady_clock::now() >= deadline) {
        cursor.deadline_hit = true;
        break;
      }
      const auto [x, y] = cursor.positions.at(
          static_cast<std::size_t>(cursor.position_index++));
      append_candidate(out, *cursor.problem, *cursor.state, cursor.block_id,
                       option, entry, x, y, cursor.prefilter_enabled);
    }
  }
  return finish();
}

CandidateBatch run_exact(RepairCursor& cursor, double remaining_seconds) {
  const auto started = std::chrono::steady_clock::now();
  if (cursor.problem == nullptr || cursor.state == nullptr)
    throw std::invalid_argument("uninitialized repair cursor");
  if (cursor.pending)
    throw std::logic_error("consume the pending candidate batch first");
  if (!std::isfinite(remaining_seconds) || remaining_seconds < 0.0)
    throw std::invalid_argument("invalid remaining_seconds");

  CandidateBatch out;
  out.state_version = cursor.state->version;
  out.pair_offsets.push_back(0);
  const auto deadline =
      started + std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                    std::chrono::duration<double>(remaining_seconds));
  const auto finish = [&]() {
    out.deadline_hit = cursor.deadline_hit;
    out.complete = cursor.complete;
    out.kernel_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - started)
                        .count();
    for (const auto& scored : cursor.best) {
      out.rows.push_back(scored.row);
      out.pair_offsets.push_back(0);
      out.total.push_back(scored.total);
      out.tardiness.push_back(scored.tardiness);
      out.assignment.push_back(scored.assignment);
      out.fragmentation.push_back(scored.fragmentation);
      out.guide_penalty.push_back(scored.guide_penalty);
    }
    return out;
  };

  if (cursor.complete || cursor.deadline_hit) return finish();
  if (!cursor.problem->exact_geometry) {
    out.exact_error = true;
    out.geos_error_count = 1;
    return finish();
  }

  while (!cursor.complete && !cursor.deadline_hit && !out.exact_error) {
    if (std::chrono::steady_clock::now() >= deadline) {
      cursor.deadline_hit = true;
      break;
    }
    if (!cursor.entry_loaded) {
      load_cursor_entry(cursor);
      if (std::chrono::steady_clock::now() >= deadline) {
        cursor.deadline_hit = true;
        break;
      }
    }
    if (cursor.position_index >=
        static_cast<std::int64_t>(cursor.positions.size())) {
      finish_cursor_entry(cursor);
      continue;
    }

    const auto& option = cursor.fitting.at(
        static_cast<std::size_t>(cursor.option_index));
    const auto entry = cursor.entries.at(
        static_cast<std::size_t>(cursor.entry_index));
    const auto& existing_rows = cursor.state->by_bay.at(option.bay);
    while (cursor.position_index <
               static_cast<std::int64_t>(cursor.positions.size()) &&
           !cursor.deadline_hit && !out.exact_error) {
      if (std::chrono::steady_clock::now() >= deadline) {
        cursor.deadline_hit = true;
        break;
      }
      const auto [x, y] = cursor.positions.at(
          static_cast<std::size_t>(cursor.position_index++));
      auto scored = scored_candidate(*cursor.problem, *cursor.state,
                                     cursor.block_id, option, entry, x, y);
      ++out.generated_candidates;
      bool accepted = true;
      for (std::size_t pair_index = 0; pair_index < existing_rows.size();
           ++pair_index) {
        const auto& existing = existing_rows[pair_index];
        if (!overlaps(scored.row, existing)) continue;
        const auto query = cursor.problem->exact_geometry->is_free(
            *cursor.problem, scored.row, existing);
        out.geos_exact_calls += query.geos_calls;
        out.aabb_skipped_pairs += query.aabb_skipped ? 1 : 0;
        if (query.cache_hit) {
          ++out.exact_cache_hits;
          out.native_cache_ns += query.exact_ns;
        } else {
          ++out.exact_cache_misses;
          if (query.verdict == ExactVerdict::ERROR) {
            out.native_error_ns += query.exact_ns;
          } else {
            out.native_exact_ns += query.exact_ns;
          }
        }
        if (query.verdict == ExactVerdict::ERROR) {
          ++out.geos_error_count;
          out.exact_error = true;
          accepted = false;
          break;
        }
        if (query.verdict == ExactVerdict::BLOCKED) {
          accepted = false;
          for (std::size_t skipped = pair_index + 1;
               skipped < existing_rows.size(); ++skipped) {
            if (overlaps(scored.row, existing_rows[skipped]))
              ++out.first_conflict_skipped_pairs;
          }
          break;
        }
      }
      if (accepted) {
        retain_top3(cursor, std::move(scored));
        ++cursor.option_accepted;
        ++cursor.accepted_total;
      }
    }
    if (!cursor.deadline_hit && !out.exact_error &&
        cursor.position_index >=
            static_cast<std::int64_t>(cursor.positions.size())) {
      finish_cursor_entry(cursor);
    }
  }
  return finish();
}

void consume(RepairCursor& cursor,
             const std::vector<std::uint8_t>& verdicts,
             std::int64_t processed_rows) {
  if (!cursor.pending)
    throw std::logic_error("repair cursor has no pending batch");
  const auto& batch = cursor.pending_batch;
  if (verdicts.size() != batch.pairs.size())
    throw std::invalid_argument("exact verdict length mismatch");
  if (processed_rows < 0 ||
      processed_rows > static_cast<std::int64_t>(batch.rows.size()))
    throw std::invalid_argument("invalid processed_rows");
  if (std::any_of(verdicts.begin(), verdicts.end(),
                  [](std::uint8_t value) { return value > 1; }))
    throw std::invalid_argument("exact verdict must be boolean");

  for (std::int64_t row_index = 0; row_index < processed_rows; ++row_index) {
    bool accepted = true;
    for (auto pair_index = batch.pair_offsets.at(
             static_cast<std::size_t>(row_index));
         pair_index < batch.pair_offsets.at(
                          static_cast<std::size_t>(row_index + 1));
         ++pair_index) {
      if (!verdicts.at(static_cast<std::size_t>(pair_index))) {
        accepted = false;
        break;
      }
    }
    if (!accepted) continue;
    const auto index = static_cast<std::size_t>(row_index);
    retain_top3(cursor, {
                            batch.rows.at(index),
                            batch.total.at(index),
                            batch.tardiness.at(index),
                            batch.assignment.at(index),
                            batch.fragmentation.at(index),
                            batch.guide_penalty.at(index),
                        });
    ++cursor.option_accepted;
    ++cursor.accepted_total;
  }

  if (processed_rows < static_cast<std::int64_t>(batch.rows.size()))
    cursor.deadline_hit = true;
  cursor.pending = false;
  cursor.pending_batch = CandidateBatch{};
  if (!cursor.deadline_hit &&
      cursor.position_index >=
          static_cast<std::int64_t>(cursor.positions.size()))
    finish_cursor_entry(cursor);
}

CandidateBatch finalize_top3(const RepairCursor& cursor) {
  if (cursor.pending)
    throw std::logic_error("cannot finalize with a pending candidate batch");
  CandidateBatch out;
  out.state_version = cursor.state ? cursor.state->version : 0;
  out.deadline_hit = cursor.deadline_hit;
  out.complete = cursor.complete;
  out.pair_offsets.push_back(0);
  for (const auto& scored : cursor.best) {
    out.rows.push_back(scored.row);
    out.pair_offsets.push_back(0);
    out.total.push_back(scored.total);
    out.tardiness.push_back(scored.tardiness);
    out.assignment.push_back(scored.assignment);
    out.fragmentation.push_back(scored.fragmentation);
    out.guide_penalty.push_back(scored.guide_penalty);
  }
  return out;
}

}  // namespace ogc_native
