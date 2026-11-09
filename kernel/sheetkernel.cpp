// sheetkernel — a small OpenCascade geometry service for the sheet metal pipeline.
//
// Python does the design and fold math; this tool turns the result into exact
// B-rep solids and answers questions about them. It reads one JSON job and
// writes one JSON result:
//
//   bodies   : planar plates (polygon outline + holes, extruded by thickness)
//              and bend sectors (annular sectors revolved about the bend axis),
//              fused into one solid per body, optionally placed by a 3x4 matrix
//   outputs  : STEP and STL files, measurements (volume, area, bounding box,
//              centre of mass, validity), the outline of a flat pattern,
//              hidden-line projections for drawings, pairwise interference and
//              clearance, and probe solids (e.g. a screw shank) intersected with
//              bodies.
//
// Usage: sheetkernel job.json

#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAlgoAPI_Common.hxx>
#include <BRepAlgoAPI_Cut.hxx>
#include <BRepAlgoAPI_Fuse.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepExtrema_DistShapeShape.hxx>
#include <BRepGProp.hxx>
#include <BRepLib.hxx>
#include <BRepMesh_IncrementalMesh.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepPrimAPI_MakeCylinder.hxx>
#include <BRepPrimAPI_MakePrism.hxx>
#include <BRepPrimAPI_MakeRevol.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <Bnd_Box.hxx>
#include <GCPnts_TangentialDeflection.hxx>
#include <GC_MakeArcOfCircle.hxx>
#include <GProp_GProps.hxx>
#include <Geom_TrimmedCurve.hxx>
#include <HLRAlgo_Projector.hxx>
#include <HLRBRep_Algo.hxx>
#include <HLRBRep_HLRToShape.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <Interface_Static.hxx>
#include <STEPControl_Reader.hxx>
#include <STEPControl_Writer.hxx>
#include <ShapeUpgrade_UnifySameDomain.hxx>
#include <Standard_Failure.hxx>
#include <StlAPI_Writer.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <array>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

// ---------------------------------------------------------------------------
// Minimal JSON reader (objects, arrays, numbers, strings, booleans, null).

struct J {
  enum Kind { NUL, NUM, STR, BOOL, ARR, OBJ } kind = NUL;
  double n = 0;
  bool b = false;
  std::string s;
  std::vector<J> a;
  std::vector<std::pair<std::string, J>> o;

  const J& operator[](const std::string& k) const {
    for (const auto& p : o)
      if (p.first == k) return p.second;
    static const J nul;
    return nul;
  }
  const J& operator[](size_t i) const { return a.at(i); }
  bool has(const std::string& k) const {
    for (const auto& p : o)
      if (p.first == k) return p.second.kind != NUL;
    return false;
  }
  size_t size() const { return a.size(); }
  double num() const {
    if (kind != NUM) throw std::runtime_error("JSON: expected a number");
    return n;
  }
  double num(double dflt) const { return kind == NUM ? n : dflt; }
  const std::string& str() const {
    if (kind != STR) throw std::runtime_error("JSON: expected a string");
    return s;
  }
  bool truthy(bool dflt = false) const { return kind == BOOL ? b : dflt; }
};

struct JParser {
  const std::string& t;
  size_t i = 0;
  explicit JParser(const std::string& text) : t(text) {}

  void ws() {
    while (i < t.size() && std::isspace(static_cast<unsigned char>(t[i]))) ++i;
  }
  [[noreturn]] void fail(const char* what) {
    throw std::runtime_error(std::string("JSON parse error: ") + what + " at offset " +
                             std::to_string(i));
  }
  J value() {
    ws();
    if (i >= t.size()) fail("unexpected end");
    char c = t[i];
    if (c == '{') return object();
    if (c == '[') return array();
    if (c == '"') {
      J v;
      v.kind = J::STR;
      v.s = string();
      return v;
    }
    if (t.compare(i, 4, "true") == 0) {
      i += 4;
      J v;
      v.kind = J::BOOL;
      v.b = true;
      return v;
    }
    if (t.compare(i, 5, "false") == 0) {
      i += 5;
      J v;
      v.kind = J::BOOL;
      return v;
    }
    if (t.compare(i, 4, "null") == 0) {
      i += 4;
      return J();
    }
    return number();
  }
  J object() {
    J v;
    v.kind = J::OBJ;
    ++i;  // {
    ws();
    if (i < t.size() && t[i] == '}') {
      ++i;
      return v;
    }
    while (true) {
      ws();
      if (i >= t.size() || t[i] != '"') fail("expected key");
      std::string k = string();
      ws();
      if (i >= t.size() || t[i] != ':') fail("expected ':'");
      ++i;
      v.o.emplace_back(k, value());
      ws();
      if (i < t.size() && t[i] == ',') {
        ++i;
        continue;
      }
      if (i < t.size() && t[i] == '}') {
        ++i;
        return v;
      }
      fail("expected ',' or '}'");
    }
  }
  J array() {
    J v;
    v.kind = J::ARR;
    ++i;  // [
    ws();
    if (i < t.size() && t[i] == ']') {
      ++i;
      return v;
    }
    while (true) {
      v.a.push_back(value());
      ws();
      if (i < t.size() && t[i] == ',') {
        ++i;
        continue;
      }
      if (i < t.size() && t[i] == ']') {
        ++i;
        return v;
      }
      fail("expected ',' or ']'");
    }
  }
  std::string string() {
    ++i;  // opening quote
    std::string out;
    while (i < t.size() && t[i] != '"') {
      char c = t[i++];
      if (c == '\\') {
        if (i >= t.size()) fail("bad escape");
        char e = t[i++];
        switch (e) {
          case 'n': out += '\n'; break;
          case 't': out += '\t'; break;
          case 'r': out += '\r'; break;
          case 'b': out += '\b'; break;
          case 'f': out += '\f'; break;
          case 'u':  // keep ASCII only; replace other code points
            if (i + 4 > t.size()) fail("bad \\u escape");
            {
              int cp = std::stoi(t.substr(i, 4), nullptr, 16);
              out += cp < 128 ? static_cast<char>(cp) : '?';
              i += 4;
            }
            break;
          default: out += e;
        }
      } else {
        out += c;
      }
    }
    if (i >= t.size()) fail("unterminated string");
    ++i;  // closing quote
    return out;
  }
  J number() {
    size_t start = i;
    if (i < t.size() && (t[i] == '-' || t[i] == '+')) ++i;
    while (i < t.size() && (std::isdigit(static_cast<unsigned char>(t[i])) || t[i] == '.' ||
                            t[i] == 'e' || t[i] == 'E' || t[i] == '-' || t[i] == '+'))
      ++i;
    if (start == i) fail("unexpected character");
    J v;
    v.kind = J::NUM;
    v.n = std::stod(t.substr(start, i - start));
    return v;
  }
};

// ---------------------------------------------------------------------------
// Small JSON writer helpers.

static std::string fmt(double x) {
  if (!std::isfinite(x)) return "null";
  char buf[64];
  std::snprintf(buf, sizeof buf, "%.10g", x);
  return buf;
}
static std::string quote(const std::string& s) {
  std::string out = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') out += '\\';
    if (c == '\n') {
      out += "\\n";
      continue;
    }
    out += c;
  }
  return out + "\"";
}
static std::string pt3(const gp_Pnt& p) {
  return "[" + fmt(p.X()) + "," + fmt(p.Y()) + "," + fmt(p.Z()) + "]";
}

// ---------------------------------------------------------------------------
// Geometry construction.

static gp_Pnt P3(const J& v) { return gp_Pnt(v[0].num(), v[1].num(), v[2].num()); }
static gp_Vec V3(const J& v) { return gp_Vec(v[0].num(), v[1].num(), v[2].num()); }

struct Frame {
  gp_Pnt o;
  gp_Vec u, v, n;
  gp_Pnt at(double a, double b, double s = 0.0) const {
    return o.Translated(u * a + v * b + n * s);
  }
};

static Frame frameFrom(const J& f) {
  Frame fr;
  fr.o = P3(f["o"]);
  fr.u = V3(f["u"]).Normalized();
  fr.v = V3(f["v"]).Normalized();
  fr.n = V3(f["n"]).Normalized();
  return fr;
}

static TopoDS_Wire polygonWire(const Frame& f, const J& pts, double s) {
  BRepBuilderAPI_MakePolygon mp;
  for (size_t k = 0; k < pts.size(); ++k) mp.Add(f.at(pts[k][0].num(), pts[k][1].num(), s));
  mp.Close();
  if (!mp.IsDone()) throw std::runtime_error("polygon wire failed");
  return mp.Wire();
}

static TopoDS_Wire circleWire(const Frame& f, double cu, double cv, double d, double s) {
  gp_Ax2 ax(f.at(cu, cv, s), gp_Dir(f.n), gp_Dir(f.u));
  gp_Circ c(ax, d / 2.0);
  return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(c).Edge()).Wire();
}

// Rounded rectangle centred at (cu, cv), size w x h, corner radius r, rotated by
// rot degrees in the face plane. r >= min(w,h)/2 gives a stadium (slot).
static TopoDS_Wire rrectWire(const Frame& f, double cu, double cv, double w, double h, double r,
                             double rotDeg, double s) {
  const double hw = w / 2.0, hh = h / 2.0;
  r = std::max(0.0, std::min(r, std::min(hw, hh)));
  const double c = std::cos(rotDeg * M_PI / 180.0), sn = std::sin(rotDeg * M_PI / 180.0);
  auto map = [&](double x, double y) {
    return f.at(cu + x * c - y * sn, cv + x * sn + y * c, s);
  };
  if (r < 1e-9) {
    BRepBuilderAPI_MakePolygon mp;
    mp.Add(map(-hw, -hh));
    mp.Add(map(hw, -hh));
    mp.Add(map(hw, hh));
    mp.Add(map(-hw, hh));
    mp.Close();
    return mp.Wire();
  }
  struct Corner {
    double cx, cy, a0;
  };
  const Corner corners[4] = {{hw - r, -hh + r, -90.0},
                             {hw - r, hh - r, 0.0},
                             {-hw + r, hh - r, 90.0},
                             {-hw + r, -hh + r, 180.0}};
  BRepBuilderAPI_MakeWire mw;
  auto arcPt = [&](const Corner& k, double deg) {
    double a = deg * M_PI / 180.0;
    return std::array<double, 2>{k.cx + r * std::cos(a), k.cy + r * std::sin(a)};
  };
  std::array<double, 2> prevEnd = arcPt(corners[3], corners[3].a0 + 90.0);
  for (const Corner& k : corners) {
    std::array<double, 2> st = arcPt(k, k.a0), md = arcPt(k, k.a0 + 45.0),
                          en = arcPt(k, k.a0 + 90.0);
    if (std::hypot(st[0] - prevEnd[0], st[1] - prevEnd[1]) > 1e-9)
      mw.Add(BRepBuilderAPI_MakeEdge(map(prevEnd[0], prevEnd[1]), map(st[0], st[1])).Edge());
    Handle(Geom_TrimmedCurve) arc =
        GC_MakeArcOfCircle(map(st[0], st[1]), map(md[0], md[1]), map(en[0], en[1])).Value();
    mw.Add(BRepBuilderAPI_MakeEdge(arc).Edge());
    prevEnd = en;
  }
  if (!mw.IsDone()) throw std::runtime_error("rounded rectangle wire failed");
  return mw.Wire();
}

static TopoDS_Wire holeWire(const Frame& f, const J& h, double s) {
  const std::string& k = h["kind"].str();
  if (k == "circle") return circleWire(f, h["c"][0].num(), h["c"][1].num(), h["d"].num(), s);
  if (k == "rrect")
    return rrectWire(f, h["c"][0].num(), h["c"][1].num(), h["w"].num(), h["h"].num(),
                     h["r"].num(0.0), h["rot"].num(0.0), s);
  if (k == "poly") return polygonWire(f, h["pts"], s);
  throw std::runtime_error("unknown hole kind: " + k);
}

static TopoDS_Shape prismOf(const TopoDS_Wire& w, const gp_Vec& dir) {
  BRepBuilderAPI_MakeFace mf(w, Standard_True);
  if (!mf.IsDone()) throw std::runtime_error("planar face failed");
  BRepPrimAPI_MakePrism pr(mf.Face(), dir);
  if (!pr.IsDone()) throw std::runtime_error("prism failed");
  return pr.Shape();
}

static TopoDS_Shape makePlate(const J& p) {
  Frame f = frameFrom(p["frame"]);
  const double T = p["T"].num();
  TopoDS_Shape plate = prismOf(polygonWire(f, p["outline"], 0.0), f.n * T);
  const J& holes = p["holes"];
  if (holes.kind == J::ARR && holes.size() > 0) {
    TopTools_ListOfShape args, tools;
    args.Append(plate);
    for (size_t k = 0; k < holes.size(); ++k)
      tools.Append(prismOf(holeWire(f, holes[k], -0.5), f.n * (T + 1.0)));
    BRepAlgoAPI_Cut cut;
    cut.SetArguments(args);
    cut.SetTools(tools);
    cut.SetFuzzyValue(1e-6);
    cut.Build();
    if (!cut.IsDone()) throw std::runtime_error("hole cut failed");
    plate = cut.Shape();
  }
  return plate;
}

// Annular sector: inner radius r_in, outer r_out, swept by `angle` (radians)
// about the axis through `base` along `axis`, starting from direction `xdir`,
// over `length` along the axis.
static TopoDS_Shape makeSector(const J& s) {
  gp_Pnt base = P3(s["base"]);
  gp_Vec ax = V3(s["axis"]).Normalized(), xd = V3(s["xdir"]).Normalized();
  const double ri = s["r_in"].num(), ro = s["r_out"].num(), L = s["length"].num(),
               ang = s["angle"].num();
  BRepBuilderAPI_MakePolygon mp;
  mp.Add(base.Translated(xd * ri));
  mp.Add(base.Translated(xd * ro));
  mp.Add(base.Translated(xd * ro + ax * L));
  mp.Add(base.Translated(xd * ri + ax * L));
  mp.Close();
  BRepBuilderAPI_MakeFace mf(mp.Wire(), Standard_True);
  BRepPrimAPI_MakeRevol rv(mf.Face(), gp_Ax1(base, gp_Dir(ax)), ang);
  if (!rv.IsDone()) throw std::runtime_error("bend sector failed");
  return rv.Shape();
}

static TopoDS_Shape fuseAll(const std::vector<TopoDS_Shape>& parts) {
  if (parts.empty()) throw std::runtime_error("body has no geometry");
  TopoDS_Shape result = parts.front();
  if (parts.size() > 1) {
    TopTools_ListOfShape args, tools;
    args.Append(parts.front());
    for (size_t k = 1; k < parts.size(); ++k) tools.Append(parts[k]);
    BRepAlgoAPI_Fuse fu;
    fu.SetArguments(args);
    fu.SetTools(tools);
    fu.SetFuzzyValue(1e-5);
    fu.Build();
    if (!fu.IsDone()) throw std::runtime_error("fuse failed");
    result = fu.Shape();
  }
  ShapeUpgrade_UnifySameDomain un(result, Standard_True, Standard_True, Standard_False);
  un.Build();
  return un.Shape();
}

static gp_Trsf trsfFrom(const J& m) {
  gp_Trsf t;
  t.SetValues(m[0][0].num(), m[0][1].num(), m[0][2].num(), m[0][3].num(),  //
              m[1][0].num(), m[1][1].num(), m[1][2].num(), m[1][3].num(),  //
              m[2][0].num(), m[2][1].num(), m[2][2].num(), m[2][3].num());
  return t;
}

struct Body {
  std::string name;
  TopoDS_Shape local;   // in part coordinates
  TopoDS_Shape placed;  // in assembly coordinates
};

// ---------------------------------------------------------------------------
// Queries.

static std::string measureJson(const TopoDS_Shape& s) {
  GProp_GProps vp, sp;
  BRepGProp::VolumeProperties(s, vp);
  BRepGProp::SurfaceProperties(s, sp);
  Bnd_Box bb;
  BRepBndLib::AddOptimal(s, bb, Standard_False, Standard_False);
  double x0, y0, z0, x1, y1, z1;
  bb.Get(x0, y0, z0, x1, y1, z1);
  int nSolids = 0, nFaces = 0, nEdges = 0;
  for (TopExp_Explorer e(s, TopAbs_SOLID); e.More(); e.Next()) ++nSolids;
  TopTools_IndexedMapOfShape fm, em;
  TopExp::MapShapes(s, TopAbs_FACE, fm);
  TopExp::MapShapes(s, TopAbs_EDGE, em);
  nFaces = fm.Extent();
  nEdges = em.Extent();
  BRepCheck_Analyzer chk(s);
  std::ostringstream o;
  o << "{\"valid\":" << (chk.IsValid() ? "true" : "false") << ",\"volume\":" << fmt(vp.Mass())
    << ",\"area\":" << fmt(sp.Mass()) << ",\"com\":" << pt3(vp.CentreOfMass())
    << ",\"bbox\":[" << fmt(x0) << "," << fmt(y0) << "," << fmt(z0) << "," << fmt(x1) << ","
    << fmt(y1) << "," << fmt(z1) << "],\"solids\":" << nSolids << ",\"faces\":" << nFaces
    << ",\"edges\":" << nEdges << "}";
  return o.str();
}

static void discretize(const TopoDS_Edge& e, std::vector<gp_Pnt>& out) {
  BRepAdaptor_Curve c(e);
  if (c.GetType() == GeomAbs_Line) {
    out.push_back(c.Value(c.FirstParameter()));
    out.push_back(c.Value(c.LastParameter()));
    return;
  }
  GCPnts_TangentialDeflection td(c, 0.08, 0.01);
  for (int k = 1; k <= td.NbPoints(); ++k) out.push_back(td.Value(k));
}

static std::string polylinesJson(const TopoDS_Shape& comp) {
  std::ostringstream o;
  o << "[";
  bool first = true;
  if (!comp.IsNull()) {
    for (TopExp_Explorer ex(comp, TopAbs_EDGE); ex.More(); ex.Next()) {
      TopoDS_Edge e = TopoDS::Edge(ex.Current());
      std::vector<gp_Pnt> pts;
      try {
        discretize(e, pts);
      } catch (const Standard_Failure&) {
        continue;
      }
      if (pts.size() < 2) continue;
      if (!first) o << ",";
      first = false;
      o << "[";
      for (size_t k = 0; k < pts.size(); ++k)
        o << (k ? "," : "") << "[" << fmt(pts[k].X()) << "," << fmt(pts[k].Y()) << "]";
      o << "]";
    }
  }
  o << "]";
  return o.str();
}

// Hidden-line projection. `toward` points from the object to the viewer and
// `right` is the drawing's +x. Results are 2D polylines in drawing coordinates.
static std::string hlrJson(const TopoDS_Shape& shape, const gp_Vec& toward, const gp_Vec& right) {
  Handle(HLRBRep_Algo) algo = new HLRBRep_Algo();
  algo->Add(shape);
  gp_Ax2 ax(gp_Pnt(0, 0, 0), gp_Dir(toward), gp_Dir(right));
  HLRAlgo_Projector proj(ax);
  algo->Projector(proj);
  algo->Update();
  algo->Hide();
  HLRBRep_HLRToShape h(algo);
  auto prep = [](TopoDS_Shape s) {
    if (!s.IsNull()) BRepLib::BuildCurves3d(s);
    return s;
  };
  std::ostringstream o;
  o << "{\"visible\":" << polylinesJson(prep(h.VCompound()))
    << ",\"outline\":" << polylinesJson(prep(h.OutLineVCompound()))
    << ",\"smooth\":" << polylinesJson(prep(h.Rg1LineVCompound()))
    << ",\"hidden\":" << polylinesJson(prep(h.HCompound()))
    << ",\"hidden_outline\":" << polylinesJson(prep(h.OutLineHCompound())) << "}";
  return o.str();
}

// Boundary of the planar face of a flat pattern lying at z == zLevel, as exact
// lines, arcs and circles (other curve types are discretised to polylines).
static std::string flatOutlineJson(const TopoDS_Shape& shape, double zLevel) {
  TopoDS_Face face;
  double bestArea = -1;
  for (TopExp_Explorer ex(shape, TopAbs_FACE); ex.More(); ex.Next()) {
    TopoDS_Face f = TopoDS::Face(ex.Current());
    BRepAdaptor_Surface s(f);
    if (s.GetType() != GeomAbs_Plane) continue;
    gp_Pln pl = s.Plane();
    if (std::abs(std::abs(pl.Axis().Direction().Z()) - 1.0) > 1e-7) continue;
    if (std::abs(pl.Location().Z() - zLevel) > 1e-5) continue;
    GProp_GProps g;
    BRepGProp::SurfaceProperties(f, g);
    if (g.Mass() > bestArea) {
      bestArea = g.Mass();
      face = f;
    }
  }
  if (face.IsNull()) throw std::runtime_error("flat outline: no planar face at requested z");
  std::ostringstream o;
  o << "{\"area\":" << fmt(bestArea) << ",\"edges\":[";
  bool first = true;
  for (TopExp_Explorer ex(face, TopAbs_EDGE); ex.More(); ex.Next()) {
    TopoDS_Edge e = TopoDS::Edge(ex.Current());
    BRepAdaptor_Curve c(e);
    if (!first) o << ",";
    first = false;
    const double t0 = c.FirstParameter(), t1 = c.LastParameter();
    if (c.GetType() == GeomAbs_Line) {
      o << "{\"type\":\"line\",\"a\":" << pt3(c.Value(t0)) << ",\"b\":" << pt3(c.Value(t1))
        << "}";
    } else if (c.GetType() == GeomAbs_Circle) {
      gp_Circ ci = c.Circle();
      const bool full = std::abs((t1 - t0) - 2 * M_PI) < 1e-7;
      // Angles measured in the global XY plane, counter-clockwise from +X.
      gp_Pnt cc = ci.Location(), p0 = c.Value(t0), p1 = c.Value(t1);
      double a0 = std::atan2(p0.Y() - cc.Y(), p0.X() - cc.X()) * 180.0 / M_PI;
      double a1 = std::atan2(p1.Y() - cc.Y(), p1.X() - cc.X()) * 180.0 / M_PI;
      const bool ccw = ci.Axis().Direction().Z() > 0;
      if (!ccw) std::swap(a0, a1);
      o << "{\"type\":\"" << (full ? "circle" : "arc") << "\",\"c\":" << pt3(cc)
        << ",\"r\":" << fmt(ci.Radius()) << ",\"a0\":" << fmt(a0) << ",\"a1\":" << fmt(a1) << "}";
    } else {
      std::vector<gp_Pnt> pts;
      discretize(e, pts);
      o << "{\"type\":\"poly\",\"pts\":[";
      for (size_t k = 0; k < pts.size(); ++k) o << (k ? "," : "") << pt3(pts[k]);
      o << "]}";
    }
  }
  o << "]}";
  return o.str();
}

static TopoDS_Shape makeProbe(const J& p) {
  const std::string& k = p["kind"].str();
  if (k == "cyl") {
    gp_Vec dir = V3(p["dir"]).Normalized();
    gp_Ax2 ax(P3(p["p"]), gp_Dir(dir));
    return BRepPrimAPI_MakeCylinder(ax, p["d"].num() / 2.0, p["len"].num()).Shape();
  }
  if (k == "box") {
    gp_Ax2 ax(P3(p["o"]), gp_Dir(V3(p["zdir"])), gp_Dir(V3(p["xdir"])));
    return BRepPrimAPI_MakeBox(ax, p["dx"].num(), p["dy"].num(), p["dz"].num()).Shape();
  }
  throw std::runtime_error("unknown probe kind: " + k);
}

static double commonVolume(const TopoDS_Shape& a, const TopoDS_Shape& b) {
  BRepAlgoAPI_Common cm(a, b);
  if (!cm.IsDone()) throw std::runtime_error("common failed");
  GProp_GProps g;
  BRepGProp::VolumeProperties(cm.Shape(), g);
  return std::abs(g.Mass());
}

static void writeStep(const std::vector<TopoDS_Shape>& shapes, const std::string& path,
                      const std::string& product) {
  STEPControl_Writer w;
  Interface_Static::SetCVal("write.step.unit", "MM");
  Interface_Static::SetCVal("write.step.product.name", product.c_str());
  TopoDS_Shape s;
  if (shapes.size() == 1) {
    s = shapes.front();
  } else {
    TopoDS_Compound c;
    BRep_Builder b;
    b.MakeCompound(c);
    for (const auto& x : shapes) b.Add(c, x);
    s = c;
  }
  if (w.Transfer(s, STEPControl_AsIs) != IFSelect_RetDone)
    throw std::runtime_error("STEP transfer failed");
  if (w.Write(path.c_str()) != IFSelect_RetDone) throw std::runtime_error("STEP write failed");
}

static void writeStl(const TopoDS_Shape& s, const std::string& path, double defl) {
  BRepMesh_IncrementalMesh mesh(s, defl, Standard_False, 0.25, Standard_True);
  StlAPI_Writer w;
  w.ASCIIMode() = Standard_False;
  if (!w.Write(s, path.c_str())) throw std::runtime_error("STL write failed");
}

// ---------------------------------------------------------------------------

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: sheetkernel job.json\n";
    return 2;
  }
  std::ifstream in(argv[1]);
  if (!in) {
    std::cerr << "cannot read " << argv[1] << "\n";
    return 2;
  }
  std::stringstream ss;
  ss << in.rdbuf();
  const std::string text = ss.str();

  std::ostringstream res;
  std::string resultPath;
  try {
    J job = JParser(text).value();
    resultPath = job["result"].str();
    std::map<std::string, Body> bodies;
    std::vector<std::string> order;

    for (size_t k = 0; k < job["bodies"].size(); ++k) {
      const J& b = job["bodies"][k];
      std::vector<TopoDS_Shape> parts;
      for (size_t i = 0; i < b["plates"].size(); ++i) parts.push_back(makePlate(b["plates"][i]));
      for (size_t i = 0; i < b["sectors"].size(); ++i)
        parts.push_back(makeSector(b["sectors"][i]));
      Body body;
      body.name = b["name"].str();
      body.local = fuseAll(parts);
      body.placed = body.local;
      if (b.has("place"))
        body.placed = BRepBuilderAPI_Transform(body.local, trsfFrom(b["place"]), Standard_True)
                          .Shape();
      bodies[body.name] = body;
      order.push_back(body.name);
    }
    auto get = [&](const J& name, bool placed) -> const TopoDS_Shape& {
      auto it = bodies.find(name.str());
      if (it == bodies.end()) throw std::runtime_error("unknown body: " + name.str());
      return placed ? it->second.placed : it->second.local;
    };

    const J& out = job["outputs"];
    res << "{\"ok\":true,\"bodies\":{";
    for (size_t k = 0; k < order.size(); ++k)
      res << (k ? "," : "") << quote(order[k]) << ":" << measureJson(bodies[order[k]].local);
    res << "}";

    res << ",\"step\":[";
    for (size_t k = 0; k < out["step"].size(); ++k) {
      const J& s = out["step"][k];
      std::vector<TopoDS_Shape> shapes;
      for (size_t i = 0; i < s["bodies"].size(); ++i)
        shapes.push_back(get(s["bodies"][i], s["placed"].truthy()));
      writeStep(shapes, s["path"].str(), s["product"].kind == J::STR ? s["product"].str() : "part");
      // Read the file back so the result proves the STEP is usable.
      STEPControl_Reader rd;
      double vol = -1.0;
      int nsol = 0;
      if (rd.ReadFile(s["path"].str().c_str()) == IFSelect_RetDone && rd.TransferRoots() > 0) {
        TopoDS_Shape back = rd.OneShape();
        GProp_GProps g;
        BRepGProp::VolumeProperties(back, g);
        vol = g.Mass();
        for (TopExp_Explorer e(back, TopAbs_SOLID); e.More(); e.Next()) ++nsol;
      }
      res << (k ? "," : "") << "{\"path\":" << quote(s["path"].str())
          << ",\"readback_volume\":" << fmt(vol) << ",\"readback_solids\":" << nsol << "}";
    }
    res << "]";

    res << ",\"stl\":[";
    for (size_t k = 0; k < out["stl"].size(); ++k) {
      const J& s = out["stl"][k];
      writeStl(get(s["body"], s["placed"].truthy()), s["path"].str(), s["deflection"].num(0.05));
      res << (k ? "," : "") << quote(s["path"].str());
    }
    res << "]";

    res << ",\"flat_outline\":{";
    for (size_t k = 0; k < out["flat_outline"].size(); ++k) {
      const J& s = out["flat_outline"][k];
      res << (k ? "," : "") << quote(s["body"].str()) << ":"
          << flatOutlineJson(get(s["body"], false), s["z"].num(0.0));
    }
    res << "}";

    res << ",\"views\":{";
    for (size_t k = 0; k < out["views"].size(); ++k) {
      const J& v = out["views"][k];
      std::vector<TopoDS_Shape> shapes;
      for (size_t i = 0; i < v["bodies"].size(); ++i)
        shapes.push_back(get(v["bodies"][i], v["placed"].truthy()));
      TopoDS_Compound c;
      BRep_Builder bb;
      bb.MakeCompound(c);
      for (const auto& x : shapes) bb.Add(c, x);
      res << (k ? "," : "") << quote(v["name"].str()) << ":"
          << hlrJson(c, V3(v["toward"]), V3(v["right"]));
    }
    res << "}";

    res << ",\"pairs\":[";
    for (size_t k = 0; k < out["pairs"].size(); ++k) {
      const J& p = out["pairs"][k];
      const TopoDS_Shape& a = get(p["a"], true);
      const TopoDS_Shape& b = get(p["b"], true);
      BRepExtrema_DistShapeShape d(a, b);
      res << (k ? "," : "") << "{\"a\":" << quote(p["a"].str()) << ",\"b\":" << quote(p["b"].str())
          << ",\"interference_volume\":" << fmt(commonVolume(a, b))
          << ",\"min_distance\":" << fmt(d.IsDone() ? d.Value() : -1.0);
      if (d.IsDone() && d.NbSolution() > 0)
        res << ",\"closest\":[" << pt3(d.PointOnShape1(1)) << "," << pt3(d.PointOnShape2(1))
            << "]";
      res << "}";
    }
    res << "]";

    res << ",\"probes\":[";
    for (size_t k = 0; k < out["probes"].size(); ++k) {
      const J& p = out["probes"][k];
      TopoDS_Shape probe = makeProbe(p);
      res << (k ? "," : "") << "{\"name\":" << quote(p["name"].str()) << ",\"hits\":{";
      for (size_t i = 0; i < p["bodies"].size(); ++i) {
        const TopoDS_Shape& b = get(p["bodies"][i], true);
        BRepExtrema_DistShapeShape d(probe, b);
        res << (i ? "," : "") << quote(p["bodies"][i].str())
            << ":{\"common_volume\":" << fmt(commonVolume(probe, b))
            << ",\"min_distance\":" << fmt(d.IsDone() ? d.Value() : -1.0) << "}";
      }
      res << "}}";
    }
    res << "]}";
  } catch (const Standard_Failure& e) {
    res.str("");
    res << "{\"ok\":false,\"error\":" << quote(std::string("OCCT: ") + e.GetMessageString())
        << "}";
  } catch (const std::exception& e) {
    res.str("");
    res << "{\"ok\":false,\"error\":" << quote(e.what()) << "}";
  }

  if (resultPath.empty()) {
    std::cout << res.str() << "\n";
  } else {
    std::ofstream o(resultPath);
    o << res.str() << "\n";
  }
  return 0;
}
