// sheetkernel: a small OpenCascade geometry service for qfold.
//
// Python owns the design (components, occurrences, align, sheet metal math).
// This tool turns it into exact B-rep geometry and answers questions about it.
// It reads one JSON job and writes one JSON result, and it is stateless: it
// knows nothing about assemblies beyond a flat list of placed instances.
//
//   bodies     prototypes in their component's coordinates, one of:
//              - sheet metal: planar plates + bend sectors, fused
//              - step: a STEP file, or one node of its product tree by path
//              - prims: boxes and cylinders, fused (stand-in hardware)
//   instances  a body placed in the world by a 3x4 matrix (an occurrence)
//   outputs    STEP (one shape, or a named assembly tree), each read back to
//              verify; STL meshes; measurements; flat-pattern outlines;
//              hidden-line views; interference and clearance between groups;
//              the product tree of STEP files, for insert.
//
// Wherever an output names geometry it may give an instance name, a body name
// (unplaced) or a list of those (one compound).
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

#include <STEPCAFControl_Reader.hxx>
#include <STEPCAFControl_Writer.hxx>
#include <TCollection_AsciiString.hxx>
#include <TCollection_ExtendedString.hxx>
#include <TDF_Label.hxx>
#include <TDF_LabelSequence.hxx>
#include <TDataStd_Name.hxx>
#include <TDocStd_Document.hxx>
#include <TopLoc_Location.hxx>
#include <XCAFApp_Application.hxx>
#include <XCAFDoc_DocumentTool.hxx>
#include <XCAFDoc_ShapeTool.hxx>

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

// Full double precision, so exact identities (e.g. fused volume == sum of
// parts) can be checked tightly on the Python side.
static std::string fmt(double x) {
  if (!std::isfinite(x)) return "null";
  char buf[64];
  std::snprintf(buf, sizeof buf, "%.17g", x);
  return buf;
}
static std::string quote(const std::string& s) {
  std::string out = "\"";
  for (unsigned char c : s) {
    if (c == '"' || c == '\\') {
      out += '\\';
      out += static_cast<char>(c);
    } else if (c < 0x20) {
      char buf[8];
      std::snprintf(buf, sizeof buf, "\\u%04x", c);
      out += buf;
    } else {
      out += static_cast<char>(c);
    }
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

// Closed outline from points [u, v] or [u, v, bulge]. A bulge makes the edge
// that starts at that point a circular arc: its middle sits bulge * chord / 2
// to the right of the chord (in u, v), so |bulge| = tan(angle / 4) as in DXF.
static TopoDS_Wire polygonWire(const Frame& f, const J& pts, double s) {
  bool arcs = false;
  for (size_t k = 0; k < pts.size(); ++k)
    if (pts[k].size() > 2 && std::abs(pts[k][2].num()) > 1e-12) arcs = true;
  if (!arcs) {
    BRepBuilderAPI_MakePolygon mp;
    for (size_t k = 0; k < pts.size(); ++k) mp.Add(f.at(pts[k][0].num(), pts[k][1].num(), s));
    mp.Close();
    if (!mp.IsDone()) throw std::runtime_error("polygon wire failed");
    return mp.Wire();
  }
  BRepBuilderAPI_MakeWire mw;
  const size_t n = pts.size();
  for (size_t k = 0; k < n; ++k) {
    const J& a = pts[k];
    const J& b = pts[(k + 1) % n];
    const double au = a[0].num(), av = a[1].num(), bu = b[0].num(), bv = b[1].num();
    const double bulge = a.size() > 2 ? a[2].num() : 0.0;
    const double du = bu - au, dv = bv - av, c = std::sqrt(du * du + dv * dv);
    if (c < 1e-12) continue;
    if (std::abs(bulge) <= 1e-12) {
      mw.Add(BRepBuilderAPI_MakeEdge(f.at(au, av, s), f.at(bu, bv, s)).Edge());
    } else {
      const double h = bulge * c / 2.0;
      const double mu = (au + bu) / 2.0 + h * dv / c, mv = (av + bv) / 2.0 - h * du / c;
      Handle(Geom_TrimmedCurve) arc =
          GC_MakeArcOfCircle(f.at(au, av, s), f.at(mu, mv, s), f.at(bu, bv, s)).Value();
      mw.Add(BRepBuilderAPI_MakeEdge(arc).Edge());
    }
  }
  if (!mw.IsDone()) throw std::runtime_error("outline wire failed");
  return mw.Wire();
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

// ---------------------------------------------------------------------------
// Primitive bodies (stand-in hardware, keep-out volumes).

static TopoDS_Shape makePrim(const J& p) {
  const std::string& k = p["kind"].str();
  if (k == "cyl") {
    gp_Ax2 ax(P3(p["p"]), gp_Dir(V3(p["dir"])));
    return BRepPrimAPI_MakeCylinder(ax, p["d"].num() / 2.0, p["len"].num()).Shape();
  }
  if (k == "box") {
    gp_Ax2 ax(P3(p["o"]), gp_Dir(V3(p["zdir"])), gp_Dir(V3(p["xdir"])));
    return BRepPrimAPI_MakeBox(ax, p["dx"].num(), p["dy"].num(), p["dz"].num()).Shape();
  }
  throw std::runtime_error("unknown primitive kind: " + k);
}

// ---------------------------------------------------------------------------
// STEP product trees (XCAF): names, local transforms, and shapes by path.

struct StepDoc {
  Handle(TDocStd_Document) doc;
  Handle(XCAFDoc_ShapeTool) st;
  TDF_LabelSequence roots;
};

static Handle(TDocStd_Document) newXcafDoc() {
  Handle(TDocStd_Document) doc;
  XCAFApp_Application::GetApplication()->NewDocument("MDTV-XCAF", doc);
  if (doc.IsNull()) throw std::runtime_error("cannot create XCAF document");
  return doc;
}

static StepDoc& loadStep(const std::string& path) {
  static std::map<std::string, StepDoc> cache;
  auto it = cache.find(path);
  if (it != cache.end()) return it->second;
  StepDoc d;
  d.doc = newXcafDoc();
  STEPCAFControl_Reader r;
  r.SetNameMode(Standard_True);
  r.SetColorMode(Standard_False);
  r.SetLayerMode(Standard_False);
  if (r.ReadFile(path.c_str()) != IFSelect_RetDone)
    throw std::runtime_error("cannot read STEP: " + path);
  if (!r.Transfer(d.doc)) throw std::runtime_error("cannot transfer STEP: " + path);
  d.st = XCAFDoc_DocumentTool::ShapeTool(d.doc->Main());
  d.st->GetFreeShapes(d.roots);
  if (d.roots.Length() == 0) throw std::runtime_error("STEP has no shapes: " + path);
  return cache.emplace(path, d).first->second;
}

static std::string labelName(const TDF_Label& l) {
  Handle(TDataStd_Name) n;
  if (!l.FindAttribute(TDataStd_Name::GetID(), n)) return "";
  TCollection_AsciiString a(n->Get(), '?');
  return a.ToCString();
}

static void setName(const TDF_Label& l, const std::string& name) {
  TDataStd_Name::Set(l, TCollection_ExtendedString(name.c_str()));
}

static std::string trsfJson(const gp_Trsf& t) {
  std::ostringstream o;
  o << "[";
  for (int r = 1; r <= 3; ++r) {
    o << (r > 1 ? "," : "") << "[";
    for (int c = 1; c <= 4; ++c) o << (c > 1 ? "," : "") << fmt(t.Value(r, c));
    o << "]";
  }
  o << "]";
  return o.str();
}

// One node of the product tree. `lab` is a free shape (a root) or a component
// label (an instance). Transforms are local to the parent node.
static void stepNodeJson(StepDoc& d, const TDF_Label& lab, bool isInstance,
                         const std::vector<int>& path, std::ostringstream& o) {
  TDF_Label proto = lab;
  gp_Trsf local;
  if (isInstance) {
    d.st->GetReferredShape(lab, proto);
    local = d.st->GetLocation(lab).Transformation();
  } else {
    local = d.st->GetShape(lab).Location().Transformation();
  }
  const bool assembly = d.st->IsAssembly(proto);
  std::string occ = labelName(lab), comp = labelName(proto);
  if (occ.empty()) occ = comp;
  o << "{\"name\":" << quote(occ) << ",\"component\":" << quote(comp)
    << ",\"transform\":" << trsfJson(local) << ",\"path\":[";
  for (size_t k = 0; k < path.size(); ++k) o << (k ? "," : "") << path[k];
  o << "],\"assembly\":" << (assembly ? "true" : "false") << ",\"children\":[";
  if (assembly) {
    TDF_LabelSequence comps;
    d.st->GetComponents(proto, comps, Standard_False);
    for (int i = 1; i <= comps.Length(); ++i) {
      std::vector<int> p2 = path;
      p2.push_back(i - 1);
      if (i > 1) o << ",";
      stepNodeJson(d, comps(i), true, p2, o);
    }
  }
  o << "]}";
}

static std::string stepTreeJson(const std::string& path) {
  StepDoc& d = loadStep(path);
  std::ostringstream o;
  o << "[";
  for (int i = 1; i <= d.roots.Length(); ++i) {
    if (i > 1) o << ",";
    stepNodeJson(d, d.roots(i), false, {i - 1}, o);
  }
  o << "]";
  return o.str();
}

// Shape of the node at `path`, in that node's own coordinates: its transform
// to the parent is not applied, because Python composes the transforms.
static TopoDS_Shape stepShape(const std::string& file, const J& path) {
  StepDoc& d = loadStep(file);
  if (path.kind != J::ARR || path.size() == 0) {
    TopoDS_Compound c;  // the whole file, root placements applied
    BRep_Builder b;
    b.MakeCompound(c);
    for (int i = 1; i <= d.roots.Length(); ++i) b.Add(c, d.st->GetShape(d.roots(i)));
    return c;
  }
  const int r = static_cast<int>(path[0].num());
  if (r < 0 || r >= d.roots.Length()) throw std::runtime_error("STEP path: bad root index");
  TDF_Label proto = d.roots(r + 1);
  for (size_t k = 1; k < path.size(); ++k) {
    TDF_LabelSequence comps;
    d.st->GetComponents(proto, comps, Standard_False);
    const int i = static_cast<int>(path[k].num());
    if (i < 0 || i >= comps.Length()) throw std::runtime_error("STEP path: bad component index");
    TDF_Label ref;
    d.st->GetReferredShape(comps(i + 1), ref);
    proto = ref;
  }
  return d.st->GetShape(proto).Located(TopLoc_Location());
}

// ---------------------------------------------------------------------------
// Named assembly export: one XCAF document from a tree of nodes
// {name, component, transform, bodies[], children[]}. Occurrences of the same
// component share one prototype, as in the source design.

static TDF_Label addTreeNode(const Handle(XCAFDoc_ShapeTool)& st, const J& node,
                             const std::map<std::string, TopoDS_Shape>& bodies,
                             std::map<std::string, TDF_Label>& protos) {
  const std::string comp = node["component"].str();
  auto hit = protos.find(comp);
  if (hit != protos.end()) return hit->second;
  const J& bl = node["bodies"];
  const J& ch = node["children"];
  auto body = [&](const J& name) -> const TopoDS_Shape& {
    auto it = bodies.find(name.str());
    if (it == bodies.end()) throw std::runtime_error("assembly STEP: unknown body " + name.str());
    return it->second;
  };
  TDF_Label lab;
  if (ch.size() == 0 && bl.size() == 1) {
    lab = st->AddShape(body(bl[0]), Standard_False);
  } else {
    lab = st->NewShape();
    for (size_t k = 0; k < bl.size(); ++k) {
      TDF_Label part = st->AddShape(body(bl[k]), Standard_False);
      setName(part, bl[k].str());
      setName(st->AddComponent(lab, part, TopLoc_Location()), bl[k].str());
    }
    for (size_t k = 0; k < ch.size(); ++k) {
      TDF_Label childProto = addTreeNode(st, ch[k], bodies, protos);
      TDF_Label inst =
          st->AddComponent(lab, childProto, TopLoc_Location(trsfFrom(ch[k]["transform"])));
      setName(inst, ch[k]["name"].str());
    }
  }
  setName(lab, comp);
  protos[comp] = lab;
  return lab;
}

static void writeStepTree(const J& tree, const std::map<std::string, TopoDS_Shape>& bodies,
                          const std::string& path) {
  Handle(TDocStd_Document) doc = newXcafDoc();
  Handle(XCAFDoc_ShapeTool) st = XCAFDoc_DocumentTool::ShapeTool(doc->Main());
  std::map<std::string, TDF_Label> protos;
  addTreeNode(st, tree, bodies, protos);
  st->UpdateAssemblies();
  Interface_Static::SetCVal("write.step.unit", "MM");
  STEPCAFControl_Writer w;
  w.SetNameMode(Standard_True);
  w.SetColorMode(Standard_False);
  if (!w.Transfer(doc, STEPControl_AsIs)) throw std::runtime_error("assembly STEP transfer failed");
  if (w.Write(path.c_str()) != IFSelect_RetDone)
    throw std::runtime_error("assembly STEP write failed");
}

// ---------------------------------------------------------------------------

static double commonVolume(const TopoDS_Shape& a, const TopoDS_Shape& b) {
  BRepAlgoAPI_Common cm(a, b);
  if (!cm.IsDone()) throw std::runtime_error("common failed");
  GProp_GProps g;
  BRepGProp::VolumeProperties(cm.Shape(), g);
  return std::abs(g.Mass());
}

static void writeStep(const TopoDS_Shape& s, const std::string& path, const std::string& product) {
  STEPControl_Writer w;
  Interface_Static::SetCVal("write.step.unit", "MM");
  Interface_Static::SetCVal("write.step.product.name", product.c_str());
  if (w.Transfer(s, STEPControl_AsIs) != IFSelect_RetDone)
    throw std::runtime_error("STEP transfer failed");
  if (w.Write(path.c_str()) != IFSelect_RetDone) throw std::runtime_error("STEP write failed");
}

// Read a written STEP back so the result proves the file is usable.
static std::string readbackJson(const std::string& path) {
  STEPControl_Reader rd;
  double vol = -1.0;
  int nsol = 0;
  if (rd.ReadFile(path.c_str()) == IFSelect_RetDone && rd.TransferRoots() > 0) {
    TopoDS_Shape back = rd.OneShape();
    GProp_GProps g;
    BRepGProp::VolumeProperties(back, g);
    vol = g.Mass();
    for (TopExp_Explorer e(back, TopAbs_SOLID); e.More(); e.Next()) ++nsol;
  }
  return "{\"path\":" + quote(path) + ",\"readback_volume\":" + fmt(vol) +
         ",\"readback_solids\":" + std::to_string(nsol) + "}";
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
  bool ok = false;
  try {
    J job = JParser(text).value();
    resultPath = job["result"].str();

    std::map<std::string, TopoDS_Shape> bodies, instances;
    std::map<std::string, double> partsVolume;  // sum over unfused parts
    std::vector<std::string> order;
    for (size_t k = 0; k < job["bodies"].size(); ++k) {
      const J& b = job["bodies"][k];
      const std::string name = b["name"].str();
      TopoDS_Shape shape;
      if (b.has("step")) {
        shape = stepShape(b["step"].str(), b["path"]);
      } else {
        std::vector<TopoDS_Shape> parts;
        for (size_t i = 0; i < b["plates"].size(); ++i) parts.push_back(makePlate(b["plates"][i]));
        for (size_t i = 0; i < b["sectors"].size(); ++i)
          parts.push_back(makeSector(b["sectors"][i]));
        for (size_t i = 0; i < b["prims"].size(); ++i) parts.push_back(makePrim(b["prims"][i]));
        // If the parts overlap anywhere, the fused volume comes out smaller
        // than this sum; that is how self-intersection is detected.
        double sum = 0.0;
        for (const auto& part : parts) {
          GProp_GProps g;
          BRepGProp::VolumeProperties(part, g);
          sum += g.Mass();
        }
        partsVolume[name] = sum;
        shape = fuseAll(parts);
      }
      if (bodies.count(name)) throw std::runtime_error("duplicate body: " + name);
      bodies[name] = shape;
      order.push_back(name);
    }
    for (size_t k = 0; k < job["instances"].size(); ++k) {
      const J& i = job["instances"][k];
      auto it = bodies.find(i["body"].str());
      if (it == bodies.end()) throw std::runtime_error("instance of unknown body: " + i["body"].str());
      instances[i["name"].str()] = it->second.Moved(TopLoc_Location(trsfFrom(i["place"])));
    }
    auto one = [&](const std::string& n) -> TopoDS_Shape {
      auto it = instances.find(n);
      if (it != instances.end()) return it->second;
      auto jt = bodies.find(n);
      if (jt != bodies.end()) return jt->second;
      throw std::runtime_error("unknown instance or body: " + n);
    };
    auto geom = [&](const J& ref) -> TopoDS_Shape {
      if (ref.kind == J::STR) return one(ref.s);
      TopoDS_Compound c;
      BRep_Builder bb;
      bb.MakeCompound(c);
      for (size_t k = 0; k < ref.size(); ++k) bb.Add(c, one(ref[k].str()));
      return c;
    };
    auto label = [&](const J& ref) -> std::string {
      if (ref.kind == J::STR) return ref.s;
      std::string s;
      for (size_t k = 0; k < ref.size(); ++k) s += (k ? "+" : "") + ref[k].str();
      return s;
    };

    const J& out = job["outputs"];
    res << "{\"ok\":true,\"bodies\":{";
    for (size_t k = 0; k < order.size(); ++k) {
      std::string m = measureJson(bodies[order[k]]);
      auto pv = partsVolume.find(order[k]);
      if (pv != partsVolume.end())
        m = m.substr(0, m.size() - 1) + ",\"parts_volume\":" + fmt(pv->second) + "}";
      res << (k ? "," : "") << quote(order[k]) << ":" << m;
    }
    res << "}";

    res << ",\"measure\":{";
    for (size_t k = 0; k < out["measure"].size(); ++k) {
      const J& m = out["measure"][k];
      res << (k ? "," : "") << quote(m["name"].str()) << ":" << measureJson(geom(m["geom"]));
    }
    res << "}";

    res << ",\"inspect_step\":{";
    for (size_t k = 0; k < out["inspect_step"].size(); ++k) {
      const std::string& p = out["inspect_step"][k].str();
      res << (k ? "," : "") << quote(p) << ":" << stepTreeJson(p);
    }
    res << "}";

    res << ",\"step\":[";
    for (size_t k = 0; k < out["step"].size(); ++k) {
      const J& s = out["step"][k];
      if (s.has("tree")) {
        writeStepTree(s["tree"], bodies, s["path"].str());
      } else {
        writeStep(geom(s["geom"]), s["path"].str(),
                  s["product"].kind == J::STR ? s["product"].str() : "part");
      }
      res << (k ? "," : "") << readbackJson(s["path"].str());
    }
    res << "]";

    res << ",\"stl\":[";
    for (size_t k = 0; k < out["stl"].size(); ++k) {
      const J& s = out["stl"][k];
      writeStl(geom(s["geom"]), s["path"].str(), s["deflection"].num(0.05));
      res << (k ? "," : "") << quote(s["path"].str());
    }
    res << "]";

    res << ",\"flat_outline\":{";
    for (size_t k = 0; k < out["flat_outline"].size(); ++k) {
      const J& s = out["flat_outline"][k];
      res << (k ? "," : "") << quote(s["body"].str()) << ":"
          << flatOutlineJson(one(s["body"].str()), s["z"].num(0.0));
    }
    res << "}";

    res << ",\"views\":{";
    for (size_t k = 0; k < out["views"].size(); ++k) {
      const J& v = out["views"][k];
      res << (k ? "," : "") << quote(v["name"].str()) << ":"
          << hlrJson(geom(v["geom"]), V3(v["toward"]), V3(v["right"]));
    }
    res << "}";

    res << ",\"pairs\":[";
    for (size_t k = 0; k < out["pairs"].size(); ++k) {
      const J& p = out["pairs"][k];
      const TopoDS_Shape a = geom(p["a"]), b = geom(p["b"]);
      res << (k ? "," : "") << "{\"a\":" << quote(label(p["a"])) << ",\"b\":"
          << quote(label(p["b"])) << ",\"interference_volume\":" << fmt(commonVolume(a, b));
      // "distance": false skips the closest-distance search (overlap only).
      if (p["distance"].truthy(true)) {
        BRepExtrema_DistShapeShape d(a, b);
        res << ",\"min_distance\":" << fmt(d.IsDone() ? d.Value() : -1.0);
        if (d.IsDone() && d.NbSolution() > 0)
          res << ",\"closest\":[" << pt3(d.PointOnShape1(1)) << "," << pt3(d.PointOnShape2(1))
              << "]";
      } else {
        res << ",\"min_distance\":null";
      }
      res << "}";
    }
    res << "]}";
    ok = true;
  } catch (const Standard_Failure& e) {
    res.str("");
    res << "{\"ok\":false,\"error\":" << quote(std::string("OCCT: ") + e.GetMessageString())
        << "}";
  } catch (const std::exception& e) {
    res.str("");
    res << "{\"ok\":false,\"error\":" << quote(e.what()) << "}";
  }

  // Errors before "result" was read (e.g. malformed JSON) go to stderr.
  if (resultPath.empty()) {
    std::cerr << res.str() << "\n";
  } else {
    std::ofstream o(resultPath);
    o << res.str() << "\n";
  }
  return ok ? 0 : 1;
}
