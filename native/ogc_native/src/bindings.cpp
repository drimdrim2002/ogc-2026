#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <geos_c.h>

#include <algorithm>
#include <optional>
#include <tuple>

#include "exact_geometry.hpp"
#include "repair_kernel.hpp"

namespace py = pybind11;

namespace {
ogc_native::Row row(const py::handle& value) { auto v=py::cast<std::vector<std::int64_t>>(value); if(v.size()!=7) throw py::value_error("row must have seven int64 values"); return {v[0],v[1],v[2],v[3],v[4],v[5],v[6]}; }
py::list rows(const std::vector<ogc_native::Row>& value) { py::list out; for(auto r:value) out.append(py::make_tuple(r.block,r.bay,r.orient,r.x,r.y,r.entry,r.exit)); return out; }
std::vector<std::optional<ogc_native::Aabb>> aabbs(const py::handle& value) { std::vector<std::optional<ogc_native::Aabb>> output; for(auto item:value.cast<py::list>()){if(item.is_none()){output.push_back(std::nullopt);continue;}auto bounds=item.cast<std::vector<double>>();if(bounds.size()!=4)throw py::value_error("AABB must have four doubles");output.push_back(ogc_native::Aabb{bounds[0],bounds[1],bounds[2],bounds[3]});}return output; }
ogc_native::Wkb wkb(const py::handle& value) {
  if (!py::isinstance<py::bytes>(value)) throw py::value_error("WKB must be bytes");
  const auto bytes = py::cast<std::string>(value);
  return ogc_native::Wkb(bytes.begin(), bytes.end());
}
ogc_native::ExactShapePayload exact_payload(const py::dict& source) {
  ogc_native::ExactShapePayload output;
  for (const auto item : source["layers_wkb"].cast<py::list>()) {
    if (item.is_none()) {
      output.layers.push_back(std::nullopt);
    } else {
      output.layers.push_back(wkb(item));
    }
  }
  for (const auto item : source["suffix_unions_wkb"].cast<py::list>()) {
    output.suffix_unions.push_back(wkb(item));
  }
  if (output.layers.size() != output.suffix_unions.size())
    throw py::value_error("layer/suffix WKB length mismatch");
  return output;
}
ogc_native::StaticProblem problem(const py::dict& source) {
  ogc_native::StaticProblem p;
  std::vector<std::vector<ogc_native::ExactShapePayload>> exact_payloads;
  auto weights = source["weights"].cast<py::dict>();
  p.w1 = weights["w1"].cast<double>();
  p.w2 = weights["w2"].cast<double>();
  p.w3 = weights["w3"].cast<double>();
  for (auto item : source["bays"].cast<py::list>()) {
    auto bay = item.cast<py::dict>();
    p.bays.push_back({bay["width"].cast<std::int64_t>(),
                      bay["height"].cast<std::int64_t>()});
  }
  for (auto item : source["blocks"].cast<py::list>()) {
    auto data = item.cast<py::dict>();
    ogc_native::Block block{
        data["release"].cast<std::int64_t>(),
        data["due"].cast<std::int64_t>(),
        data["dwell"].cast<std::int64_t>(),
        data["workload"].cast<double>(),
        data["preferences"].cast<std::vector<double>>(),
        {},
    };
    std::vector<ogc_native::ExactShapePayload> block_payloads;
    for (auto orientation_item : data["orientations"].cast<py::list>()) {
      auto orientation = orientation_item.cast<py::dict>();
      ogc_native::Shape shape{
          orientation["xmin"].cast<double>(),
          orientation["ymin"].cast<double>(),
          orientation["xmax"].cast<double>(),
          orientation["ymax"].cast<double>(),
          {},
          aabbs(orientation["layer_aabbs"]),
          aabbs(orientation["suffix_aabbs"]),
      };
      if (shape.layer_aabbs.size() != shape.suffix_aabbs.size())
        throw py::value_error("layer/suffix AABB length mismatch");
      for (auto point : orientation["vertices"].cast<py::list>()) {
        auto xy = point.cast<std::vector<double>>();
        if (xy.size() != 2) throw py::value_error("vertex");
        shape.vertices.push_back({xy[0], xy[1]});
      }
      auto payload = exact_payload(orientation);
      if (payload.layers.size() != shape.layer_aabbs.size())
        throw py::value_error("WKB/AABB length mismatch");
      block.orientations.push_back(std::move(shape));
      block_payloads.push_back(std::move(payload));
    }
    p.blocks.push_back(std::move(block));
    exact_payloads.push_back(std::move(block_payloads));
  }
  p.exact_geometry =
      std::make_shared<ogc_native::ExactGeometryStore>(std::move(exact_payloads));
  return p;
}
ogc_native::PackedState packed_state(py::list source, std::vector<double> loads, std::int64_t version, std::size_t bays) { ogc_native::PackedState out; out.loads=std::move(loads);out.version=version;out.by_bay.resize(bays);for(auto item:source){auto value=row(item);if(value.bay<0||static_cast<std::size_t>(value.bay)>=bays)throw py::value_error("invalid bay");out.rows.push_back(value);out.by_bay[value.bay].push_back(value);}for(auto& interval:out.by_bay)std::sort(interval.begin(),interval.end(),[](const auto&a,const auto&b){return std::tie(a.entry,a.exit,a.block)<std::tie(b.entry,b.exit,b.block);});return out; }
}

PYBIND11_MODULE(_ogc_native, module) {
  module.doc() = "OGC-SAGE native repair kernel with Phase-1 streaming cursor";
  module.def("empty_kernel_info", [] {
    const auto info = ogc_native::empty_kernel_info();
    py::dict result;
    result["name"] = py::str(info.name);
    result["api_version"] = info.api_version;
    result["repair_api_version"] = 5;
    result["geos_version"] = py::str(GEOSversion());
    return result;
  });
  py::class_<ogc_native::StaticProblem>(module, "StaticProblem")
      .def(py::init([](py::dict source) { return problem(source); }))
      .def("exact_verdict", [](ogc_native::StaticProblem& source,
                                const py::handle& first,
                                const py::handle& second) {
        if (!source.exact_geometry) throw py::value_error("exact geometry is unavailable");
        const auto result = source.exact_geometry->is_free(
            source, row(first), row(second));
        return py::make_tuple(
            py::str(ogc_native::exact_verdict_name(result.verdict)),
            result.cache_hit, result.exact_ns);
      });
  py::class_<ogc_native::PackedState>(module, "PackedState")
      .def(py::init([](py::list rows, std::vector<double> loads, std::int64_t version, std::size_t bay_count) { return packed_state(rows, std::move(loads), version, bay_count); }))
      .def_readonly("version", &ogc_native::PackedState::version);
  py::class_<ogc_native::Prepared>(module, "PreparedCandidateBatch")
      .def_readonly("state_version", &ogc_native::Prepared::state_version)
      .def_readonly("kernel_ns", &ogc_native::Prepared::kernel_ns)
      .def_readonly("fitting_option_index", &ogc_native::Prepared::fitting_option_index)
      .def_readonly("entry_index", &ogc_native::Prepared::entry_index)
      .def_readonly("fitting_option_count", &ogc_native::Prepared::fitting_option_count)
      .def_readonly("entry_count", &ogc_native::Prepared::entry_count)
      .def_readonly("deadline_hit", &ogc_native::Prepared::deadline_hit)
      .def_property_readonly("rows", [](const ogc_native::Prepared& p) { return rows(p.rows); })
      .def_property_readonly("pair_offsets", [](const ogc_native::Prepared& p) { return p.pair_offsets; })
      .def_property_readonly("pairs", [](const ogc_native::Prepared& p) { return rows(p.pairs); })
      .def_property_readonly("pair_definitely_free", [](const ogc_native::Prepared& p) { return p.pair_definitely_free; })
      .def("finalize", [](const ogc_native::Prepared& p, const std::vector<std::uint8_t>& verdicts) { if(verdicts.size()!=p.pairs.size()) throw py::value_error("exact verdict length"); std::vector<std::int64_t> accepted; for(std::size_t i=0;i<p.rows.size();++i){bool ok=true;for(auto j=p.pair_offsets[i];j<p.pair_offsets[i+1];++j)if(!verdicts[j]){ok=false;break;}if(ok)accepted.push_back(i);}std::sort(accepted.begin(),accepted.end(),[&](auto left,auto right){const auto&a=p.rows[left];const auto&b=p.rows[right];return std::tie(p.total[left],p.fragmentation[left],p.guide_penalty[left],a.exit,a.entry,a.bay,a.orient,a.x,a.y,a.block)<std::tie(p.total[right],p.fragmentation[right],p.guide_penalty[right],b.exit,b.entry,b.bay,b.orient,b.x,b.y,b.block);});if(accepted.size()>3)accepted.resize(3);return accepted; })
      .def_property_readonly("total", [](const ogc_native::Prepared& p) { return p.total; })
      .def_property_readonly("tardiness", [](const ogc_native::Prepared& p) { return p.tardiness; })
      .def_property_readonly("assignment", [](const ogc_native::Prepared& p) { return p.assignment; })
      .def_property_readonly("fragmentation", [](const ogc_native::Prepared& p) { return p.fragmentation; })
      .def_property_readonly("guide_penalty", [](const ogc_native::Prepared& p) { return p.guide_penalty; });
  py::class_<ogc_native::CandidateBatch>(module, "CandidateBatch")
      .def_readonly("state_version", &ogc_native::CandidateBatch::state_version)
      .def_readonly("kernel_ns", &ogc_native::CandidateBatch::kernel_ns)
      .def_readonly("generated_candidates", &ogc_native::CandidateBatch::generated_candidates)
      .def_readonly("aabb_skipped_pairs", &ogc_native::CandidateBatch::aabb_skipped_pairs)
      .def_readonly("geos_exact_calls", &ogc_native::CandidateBatch::geos_exact_calls)
      .def_readonly("first_conflict_skipped_pairs", &ogc_native::CandidateBatch::first_conflict_skipped_pairs)
      .def_readonly("exact_cache_hits", &ogc_native::CandidateBatch::exact_cache_hits)
      .def_readonly("exact_cache_misses", &ogc_native::CandidateBatch::exact_cache_misses)
      .def_readonly("geos_error_count", &ogc_native::CandidateBatch::geos_error_count)
      .def_readonly("native_exact_ns", &ogc_native::CandidateBatch::native_exact_ns)
      .def_readonly("native_cache_ns", &ogc_native::CandidateBatch::native_cache_ns)
      .def_readonly("native_error_ns", &ogc_native::CandidateBatch::native_error_ns)
      .def_readonly("exact_error", &ogc_native::CandidateBatch::exact_error)
      .def_readonly("deadline_hit", &ogc_native::CandidateBatch::deadline_hit)
      .def_readonly("complete", &ogc_native::CandidateBatch::complete)
      .def_property_readonly("rows", [](const ogc_native::CandidateBatch& batch) { return rows(batch.rows); })
      .def_property_readonly("pair_offsets", [](const ogc_native::CandidateBatch& batch) { return batch.pair_offsets; })
      .def_property_readonly("pairs", [](const ogc_native::CandidateBatch& batch) { return rows(batch.pairs); })
      .def_property_readonly("total", [](const ogc_native::CandidateBatch& batch) { return batch.total; })
      .def_property_readonly("tardiness", [](const ogc_native::CandidateBatch& batch) { return batch.tardiness; })
      .def_property_readonly("assignment", [](const ogc_native::CandidateBatch& batch) { return batch.assignment; })
      .def_property_readonly("fragmentation", [](const ogc_native::CandidateBatch& batch) { return batch.fragmentation; })
      .def_property_readonly("guide_penalty", [](const ogc_native::CandidateBatch& batch) { return batch.guide_penalty; });
  py::class_<ogc_native::RepairCursor>(module, "RepairCursor")
      .def(py::init([](const ogc_native::StaticProblem& problem,
                       const ogc_native::PackedState& state,
                       std::int64_t block_id, py::object current,
                       std::int64_t time_cap, std::int64_t anchor_cap,
                       std::int64_t lattice_cap, bool prefilter_enabled,
                       bool lattice_only) {
        std::optional<ogc_native::Row> current_row;
        if (!current.is_none()) current_row = row(current);
        return ogc_native::make_repair_cursor(
            problem, state, block_id,
            current_row ? &*current_row : nullptr, time_cap,
            lattice_only ? lattice_cap : anchor_cap, prefilter_enabled,
            lattice_only);
      }), py::keep_alive<1, 2>(), py::keep_alive<1, 3>(),
      py::arg("problem"), py::arg("state"), py::arg("block_id"),
      py::arg("current") = py::none(), py::arg("time_cap") = 12,
      py::arg("anchor_cap") = 48, py::arg("lattice_cap") = 512,
      py::arg("prefilter_enabled") = false,
      py::arg("lattice_only") = false)
      .def("next_batch", [](ogc_native::RepairCursor& cursor,
                            std::int64_t max_rows,
                            double remaining_seconds) {
        return ogc_native::next_batch(cursor, max_rows, remaining_seconds);
      }, py::arg("max_rows") = 16, py::arg("remaining_seconds") = 0.0)
      .def("run_exact", [](ogc_native::RepairCursor& cursor,
                            double remaining_seconds) {
        py::gil_scoped_release release;
        return ogc_native::run_exact(cursor, remaining_seconds);
      }, py::arg("remaining_seconds") = 0.0)
      .def("consume", [](ogc_native::RepairCursor& cursor,
                         const std::vector<std::uint8_t>& verdicts,
                         std::int64_t processed_rows) {
        ogc_native::consume(cursor, verdicts, processed_rows);
      }, py::arg("verdicts"), py::arg("processed_rows"))
      .def("finalize_top3", [](const ogc_native::RepairCursor& cursor) {
        return ogc_native::finalize_top3(cursor);
      })
      .def_property_readonly("state_version", [](const ogc_native::RepairCursor& cursor) {
        return cursor.state == nullptr ? std::int64_t{0} : cursor.state->version;
      })
      .def_readonly("prepare_ns", &ogc_native::RepairCursor::prepare_ns)
      .def_readonly("accepted_total", &ogc_native::RepairCursor::accepted_total)
      .def_readonly("deadline_hit", &ogc_native::RepairCursor::deadline_hit)
      .def_readonly("complete", &ogc_native::RepairCursor::complete);
  module.def("prepare_candidates", [](const ogc_native::StaticProblem& p, const ogc_native::PackedState& state, std::int64_t block_id, py::object current, std::int64_t time_cap, std::int64_t anchor_cap, std::int64_t attempt_cap, bool prefilter_enabled) { std::optional<ogc_native::Row> current_row;if(!current.is_none())current_row=row(current);return ogc_native::prepare_candidates(p,state,block_id,current_row?&*current_row:nullptr,time_cap,anchor_cap,attempt_cap,prefilter_enabled); }, py::arg("problem"),py::arg("state"),py::arg("block_id"),py::arg("current")=py::none(),py::arg("time_cap")=12,py::arg("anchor_cap")=48,py::arg("attempt_cap")=-1,py::arg("prefilter_enabled")=false);
  module.def("prepare_candidate_chunk", [](const ogc_native::StaticProblem& p, const ogc_native::PackedState& state, std::int64_t block_id, py::object current, std::int64_t time_cap, std::int64_t anchor_cap, bool prefilter_enabled, std::int64_t fitting_option_index, std::int64_t entry_index, bool lattice_only, double remaining_seconds) { std::optional<ogc_native::Row> current_row;if(!current.is_none())current_row=row(current);return ogc_native::prepare_candidate_chunk(p,state,block_id,current_row?&*current_row:nullptr,time_cap,anchor_cap,prefilter_enabled,fitting_option_index,entry_index,lattice_only,remaining_seconds); }, py::arg("problem"),py::arg("state"),py::arg("block_id"),py::arg("current")=py::none(),py::arg("time_cap")=12,py::arg("anchor_cap")=48,py::arg("prefilter_enabled")=false,py::arg("fitting_option_index"),py::arg("entry_index"),py::arg("lattice_only")=false,py::arg("remaining_seconds"));
}
