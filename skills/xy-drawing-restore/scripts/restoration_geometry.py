# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Geometry helpers for source-derived restoration in an isolated local workspace."""
import math


def bounded_bezier_polyline(control_points, tolerance_mm):
    """Approximate an analytic cubic with a two-sided convex-hull error bound.

    Each accepted leaf has all controls inside the tolerance capsule of its
    endpoint chord. Convexity bounds curve->chord; continuous projection from
    one chord endpoint to the other also bounds chord->curve. No raster fit.
    """
    from ezdxf.math import Vec2
    controls=[Vec2(p) for p in control_points]
    if (len(controls)!=4 or not math.isfinite(tolerance_mm) or tolerance_mm<=0
            or not all(math.isfinite(v) for p in controls for v in p)):
        raise ValueError('Finite cubic controls and positive tolerance required')
    result=[controls[0]]
    def split(cp,depth):
        a,b,c,d=cp;delta=d-a;length2=delta.dot(delta)
        def distance(p):
            t=max(0,min(1,(p-a).dot(delta)/length2)) if length2 else 0
            return (p-a-delta*t).magnitude
        if max(distance(p) for p in cp)<=tolerance_mm:
            result.append(d);return
        if depth>=32 or len(result)>65536:
            raise ValueError('Cubic approximation exceeds bounded capacity')
        ab=(a+b)*.5;bc=(b+c)*.5;cd=(c+d)*.5
        abc=(ab+bc)*.5;bcd=(bc+cd)*.5;mid=(abc+bcd)*.5
        split([a,ab,abc,mid],depth+1);split([mid,bcd,cd,d],depth+1)
    split(controls,0)
    return result


def _bounded_pdf_arc(curves, budget):
    """Return a native arc only when the source cubic chain stays within budget."""
    import numpy as np

    if any(not np.array_equal(a[-1], b[0]) for a, b in zip(curves, curves[1:])):
        return None
    chord_budget = budget / 4
    points = [curves[0][0]]
    for controls in curves:
        points.extend(tuple(p) for p in bounded_bezier_polyline(controls, chord_budget)[1:])
    points = np.asarray(points)
    closed = np.array_equal(points[0], points[-1])
    samples = points[:-1] if closed else points
    if len(samples) < 5:
        return None
    origin = samples.mean(axis=0)
    local = samples - origin
    solution, _, rank, _ = np.linalg.lstsq(
        np.c_[2 * local, np.ones(len(local))], np.square(local).sum(axis=1), rcond=None)
    if rank != 3:
        return None
    center = origin + solution[:2]
    radial = points - center
    distances = np.linalg.norm(radial, axis=1)
    radius = float(distances.mean())
    if radius <= budget:
        return None
    angles = np.unwrap(np.arctan2(radial[:, 1], radial[:, 0]))
    turns = np.diff(angles)
    if not (np.all(turns > 0) or np.all(turns < 0)):
        return None
    sweep = abs(angles[-1] - angles[0])
    if (closed and not math.isclose(sweep, math.tau, abs_tol=1e-9)) or (not closed and sweep >= math.tau):
        return None
    # Match each source chord to its radial projection on the circle. The
    # endpoint shifts bound chord-to-chord error. Sagitta bounds the arc gap.
    sagitta = 2 * radius * math.sin(float(np.max(np.abs(turns))) / 4) ** 2
    error = chord_budget + float(np.max(np.abs(distances - radius))) + sagitta
    if error > budget:
        return None
    start, end = (angles[0], angles[-1]) if turns[0] > 0 else (angles[-1], angles[0])
    return center, radius, math.degrees(start) % 360, math.degrees(end) % 360, closed, error


def add_cubic_chain(layout, curves, tolerance_mm, *, dxfattribs=None):
    """Write one contiguous, source-derived curve without spline entities."""
    import numpy as np

    controls = [np.asarray(curve, dtype=float) for curve in curves]
    if (not controls or not math.isfinite(tolerance_mm) or tolerance_mm <= 0
            or any(curve.shape != (4, 2) or not np.isfinite(curve).all() for curve in controls)
            or any(not np.array_equal(a[-1], b[0]) for a, b in zip(controls, controls[1:]))):
        raise ValueError('Cubic paths must have finite controls and contiguous endpoints')
    attrs = dict(dxfattribs or {})
    if attrs.get('linetype', 'CONTINUOUS').upper() == 'CONTINUOUS':
        fitted = _bounded_pdf_arc(controls, tolerance_mm)
        if fitted is not None:
            center, radius, start, end, closed, error = fitted
            if closed:
                return layout.add_circle(center, radius, dxfattribs=attrs), error
            return layout.add_arc(center, radius, start, end, dxfattribs=attrs), error
    vertices = []
    for curve in controls:
        points = bounded_bezier_polyline(curve, tolerance_mm)
        vertices.extend(points if not vertices else points[1:])
    if all(p == vertices[0] for p in vertices):
        raise ValueError('Degenerate source path')
    closed = vertices[-1] == vertices[0]
    entity = layout.add_lwpolyline(vertices[:-1] if closed else vertices, close=closed, dxfattribs=attrs)
    return entity, tolerance_mm


def add_native_linear_dimension(layout, p1, p2, base, reading_mm, *, tolerance_mm=.05,
                                angle_degrees=None, override=None, dxfattribs=None):
    """Render a dimension from the actual source feature points and CAD reading."""
    from ezdxf.math import Vec2

    p1, p2, base = Vec2(p1), Vec2(p2), Vec2(base)
    values = [*p1, *p2, *base, reading_mm, tolerance_mm]
    if not all(math.isfinite(value) for value in values) or tolerance_mm < 0 or reading_mm <= 0:
        raise ValueError('A native dimension requires finite source values')
    delta = p2 - p1
    if angle_degrees is None:
        angle_degrees = delta.angle_deg
    if not math.isfinite(angle_degrees):
        raise ValueError('Invalid source dimension direction')
    actual = abs(delta.dot(Vec2.from_deg_angle(angle_degrees)))
    if actual <= 0 or abs(actual-reading_mm) > tolerance_mm:
        raise ValueError('Actual dimension definition points differ from the source reading')
    settings = dict(override or {})
    if settings.get('dimlfac', 1) != 1:
        raise ValueError('This helper measures the actual unprojected geometry')
    dimension = layout.add_linear_dim(base, p1, p2, text='<>', angle=angle_degrees,
                                      override=settings, dxfattribs=dxfattribs)
    dimension.render()
    return dimension.dimension


def restore_raster_ornament(layout, grayscale, region, pixel_to_cad, *, threshold=145.5,
                            simplify_pixels=.12, dxfattribs=None):
    """Vectorize an observed ornament's ink contours, retaining loops and gaps."""
    import numpy as np
    from contourpy import contour_generator
    from shapely.geometry import Polygon

    image = np.asarray(grayscale)
    if image.ndim != 2 or len(region) != 4 or any(int(v) != v for v in region):
        raise ValueError('Select an integer region in the original grayscale image')
    left, top, right, bottom = map(int, region)
    if not (0 <= left < right <= image.shape[1] and 0 <= top < bottom <= image.shape[0]
            and 0 < threshold < 255 and 0 <= simplify_pixels <= .25):
        raise ValueError('Invalid original image region or contour settings')
    if (right-left)*(bottom-top) > 2_000_000:
        raise ValueError('Select the actual ornament region before recovering its contours')
    pixels = image[top:bottom, left:right]
    if not np.isfinite(pixels).all() or pixels.min() < 0 or pixels.max() > 255:
        raise ValueError('Grayscale pixels must be finite and in the 0–255 range')
    border = np.r_[pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]]
    padded = np.pad(pixels, 1, constant_values=255)
    generator = contour_generator(x=np.arange(left-1, right+1), y=np.arange(top-1, bottom+1),
                                  z=padded, fill_type='OuterOffset')
    contours, offsets = generator.filled(-1, threshold)
    polygons = []
    for points, ends in zip(contours, offsets):
        loops = [points[a:b] for a, b in zip(ends, ends[1:])]
        polygon = Polygon(loops[0], loops[1:])
        if not polygon.is_valid:
            raise ValueError('Ambiguous ornament contour; inspect the source region before fitting')
        simplified = polygon.simplify(simplify_pixels, preserve_topology=True)
        if len(simplified.interiors) != len(polygon.interiors):
            simplified = polygon
        rings = [simplified.exterior, *simplified.interiors]
        mapped = [np.asarray(pixel_to_cad(np.asarray(ring.coords[:-1])), dtype=float) for ring in rings]
        if any(v.ndim != 2 or v.shape[1] != 2 or not np.isfinite(v).all() for v in mapped):
            raise ValueError('Ornament contours need finite mapped CAD points')
        if not Polygon(mapped[0], mapped[1:]).is_valid:
            raise ValueError('Ornament transform changes contour topology')
        polygons.append(mapped)
    handles = []
    for loops in polygons:
        hatch = layout.add_hatch(dxfattribs=dxfattribs)
        hatch.dxf.hatch_style = 0
        for index, loop in enumerate(loops):
            hatch.paths.add_polyline_path(loop.tolist(), is_closed=True, flags=1 if index == 0 else 0)
        handles.append(hatch.dxf.handle)
    return {'source_region': list(region), 'threshold': threshold, 'handles': handles,
            'connected_ink_regions': len(polygons), 'holes': sum(len(loops)-1 for loops in polygons),
            'touches_region_border': bool((border < threshold).any()), 'not_a_quality_review': True}


def dimension_measurement(entity):
    """Read native definition geometry, never group 42 or display text as truth.

    DXF points 10/13/14/15 are WCS, angular point 16 and angles are OCS.
    Two-line angular dimensions select the sector containing their arc point;
    three-point angular dimensions can also select a reflex sector. Unsupported
    or degenerate definitions are explicit unknowns, not fabricated zeroes.
    """
    from ezdxf.math import Vec3

    dxf = entity.dxf
    kind = int(dxf.dimtype) & 15
    ocs = entity.ocs()

    def point(name, *, in_ocs=False):
        if not dxf.hasattr(name):
            raise ValueError('Missing native dimension definition: ' + name)
        value = Vec3(dxf.get(name))
        if not all(math.isfinite(v) for v in value):
            raise ValueError('Nonfinite native dimension definition: ' + name)
        return value if in_ocs else ocs.from_wcs(value)

    def planar_delta(a, b):
        delta = a - b
        if abs(delta.z) > 1e-7:
            raise NotImplementedError('Noncoplanar dimension definition needs spatial review')
        return delta

    def angle(vector):
        if math.hypot(vector.x, vector.y) <= 1e-12:
            raise ValueError('Degenerate angular dimension ray')
        return math.atan2(vector.y, vector.x) % math.tau

    if kind in (0, 1):
        delta = planar_delta(point('defpoint3'), point('defpoint2'))
        if kind == 1:
            if delta.magnitude <= 1e-12:
                raise ValueError('Degenerate aligned dimension definition')
            value = delta.magnitude  # Aligned: group 50 is not a projection axis.
        else:
            rotation = math.radians(float(dxf.get('angle', 0)))
            value = abs(delta.x * math.cos(rotation) + delta.y * math.sin(rotation))
    elif kind in (3, 4):
        value = planar_delta(point('defpoint4'), point('defpoint')).magnitude
    elif kind == 6:
        delta = planar_delta(point('defpoint2'), point('defpoint'))
        rotation = math.radians(-float(dxf.get('horizontal_direction', 0)))
        if not (int(dxf.dimtype) & 64):
            rotation += math.pi / 2
        value = abs(delta.x * math.cos(rotation) + delta.y * math.sin(rotation))
    elif kind == 5:
        center = point('defpoint4')
        start = angle(planar_delta(point('defpoint2'), center))
        end = angle(planar_delta(point('defpoint3'), center))
        arc = angle(planar_delta(point('defpoint'), center))
        span = (end - start) % math.tau
        position = (arc - start) % math.tau
        if min(span, math.tau - span, position, abs(position - span)) < 1e-10:
            raise ValueError('Angular sector is degenerate or its arc point is on an extension ray')
        value = math.degrees(span if position < span else math.tau - span)
    elif kind == 2:
        a, b = point('defpoint2'), point('defpoint3')
        c, d = point('defpoint4'), point('defpoint')
        u, v = planar_delta(b, a), planar_delta(d, c)
        planar_delta(c, a)
        cross = u.x * v.y - u.y * v.x
        if u.magnitude <= 1e-12 or v.magnitude <= 1e-12 or abs(cross) <= 1e-12 * u.magnitude * v.magnitude:
            raise ValueError('Parallel or degenerate angular dimension lines')
        offset = c - a
        center = a + u * ((offset.x * v.y - offset.y * v.x) / cross)
        arc = angle(planar_delta(point('defpoint5', in_ocs=True), center))
        rays = sorted([angle(u), (angle(u) + math.pi) % math.tau,
                       angle(v), (angle(v) + math.pi) % math.tau])
        value = None
        for index, start in enumerate(rays):
            span = (rays[(index + 1) % 4] - start) % math.tau
            position = (arc - start) % math.tau
            if 1e-10 < position < span - 1e-10:
                value = math.degrees(span)
                break
        if value is None:
            raise ValueError('Angular arc point is on an extension ray')
    else:
        raise NotImplementedError('Unsupported native DIMENSION type: ' + str(kind))
    if not math.isfinite(value):
        raise ValueError('Nonfinite native dimension measurement')
    return float(value)
