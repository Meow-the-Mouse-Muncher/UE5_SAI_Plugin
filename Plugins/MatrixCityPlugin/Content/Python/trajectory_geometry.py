"""Pure NumPy trajectory geometry; no Unreal dependency or rendering side effects.

All paths contain 25 observation poses. Frame numbers are sampling indices,
not physical flight timestamps. Euler tuples follow UE's (roll, pitch, yaw).
"""
import math
import random

import numpy as np

NUM_VIEWS = 25


def _validate(count, height, span=None):
    if count != NUM_VIEWS:
        raise ValueError('The dataset protocol requires exactly 25 observations')
    if not math.isfinite(height) or height <= 0:
        raise ValueError('Height/radius must be finite and positive')
    if span is not None and (not math.isfinite(span) or not 0 < span < 180):
        raise ValueError('Angular span must be between 0 and 180 degrees')


def canonical_pose(target, height, base_yaw):
    """One exact reference pose for all trajectories of a matched scene/target."""
    return (np.asarray(target, dtype=float) + [0., 0., float(height)],
            np.array([0., -90., float(base_yaw)]))


def rotation_matrix(rotation):
    roll, pitch, yaw = np.deg2rad(rotation)
    cy, sy, cp, sp, cr, sr = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), math.sin(roll)
    return np.array([[cy, -sy, 0.], [sy, cy, 0.], [0., 0., 1.]]) @ np.array(
        [[cp, 0., -sp], [0., 1., 0.], [sp, 0., cp]]) @ np.array(
        [[1., 0., 0.], [0., cr, sr], [0., -sr, cr]])


def _wrap(angle):
    return (angle + 180.) % 360. - 180.


def _rotation_angles(matrix, base_yaw):
    horizontal = math.hypot(matrix[0, 0], matrix[1, 0])
    if horizontal < 1e-10:
        # The minimal-tilt frame is exactly canonical at the zenith.
        return np.array([0., -90., float(base_yaw)])
    pitch = math.degrees(math.atan2(matrix[2, 0], horizontal))
    yaw = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    roll = math.degrees(math.atan2(-matrix[2, 1], matrix[2, 2]))
    first = np.array([_wrap(roll), pitch, base_yaw + _wrap(yaw - base_yaw)])
    second = np.array([_wrap(roll + 180.), -180. - pitch,
                       base_yaw + _wrap(yaw + 180. - base_yaw)])
    # Either branch encodes the same matrix. Prefer the one near canonical.
    def cost(value):
        return value[0] ** 2 + (value[2] - base_yaw) ** 2 + .25 * (value[1] + 90.) ** 2
    return min((first, second), key=cost)


def target_rotations(positions, target, base_yaw):
    """Minimal tilt from a common nadir frame, without the LookAt pole flip.

    A global world-Z LookAt up vector is singular at the zenith. Instead, rotate
    the complete canonical camera frame from downward to the viewing direction.
    This is smooth over the upper hemisphere. The returned Euler coordinates
    may change chart, so UE sections must use quaternion interpolation.
    """
    reference = rotation_matrix((0., -90., base_yaw))
    downward = np.array([0., 0., -1.])
    rotations = []
    for position in positions:
        direction = np.asarray(target) - position
        direction /= np.linalg.norm(direction)
        cosine = float(np.dot(downward, direction))
        if cosine <= 0:
            raise ValueError('Target-tracking cameras must remain above the target')
        axis = np.cross(downward, direction)
        x, y, z = axis
        skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
        tilt = np.eye(3) + skew + skew @ skew / (1. + cosine)
        rotations.append(_rotation_angles(tilt @ reference, base_yaw))
    return np.asarray(rotations)


def _reference(positions, rotations, target, height, base_yaw, index):
    positions[index], rotations[index] = canonical_pose(target, height, base_yaw)
    return positions, rotations


def _rotate_xy(points, base_yaw):
    angle = math.radians(base_yaw)
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0.],
                         [math.sin(angle), math.cos(angle), 0.], [0., 0., 1.]])
    return np.asarray(points) @ rotation.T


def line(target, count, height, length, base_yaw):
    _validate(count, height)
    if not math.isfinite(length) or length <= 0:
        raise ValueError('Line length must be finite and positive')
    points = np.column_stack((np.linspace(-length / 2., length / 2., count),
                              np.zeros(count), np.full(count, height)))
    positions = _rotate_xy(points, base_yaw) + target
    rotations = np.tile([0., -90., base_yaw], (count, 1))
    return _reference(positions, rotations, target, height, base_yaw, count // 2)


def planar(target, count, height, side, base_yaw):
    _validate(count, height)
    if not math.isfinite(side) or side <= 0:
        raise ValueError('Grid side must be finite and positive')
    points = []
    for row in range(5):
        for col in (range(5) if row % 2 == 0 else range(4, -1, -1)):
            points.append(((col - 2) * side / 4., (2 - row) * side / 4., height))
    positions = _rotate_xy(points, base_yaw) + target
    rotations = np.tile([0., -90., base_yaw], (count, 1))
    return _reference(positions, rotations, target, height, base_yaw, count // 2)


def arc(target, count, radius, span, base_yaw):
    _validate(count, radius, span)
    angles = np.linspace(90. - span / 2., 90. + span / 2., count)
    radians = np.deg2rad(angles)
    points = np.column_stack((radius * np.cos(radians), np.zeros(count), radius * np.sin(radians)))
    positions = _rotate_xy(points, base_yaw) + target
    # Signed pitch crosses -90 continuously; yaw/roll never flip at the top.
    rotations = np.column_stack((np.zeros(count), angles - 180., np.full(count, base_yaw)))
    return _reference(positions, rotations, target, radius, base_yaw, count // 2)


def tracking_line(target, count, height, span, base_yaw):
    _validate(count, height, span)
    angles = np.deg2rad(np.linspace(-span / 2., span / 2., count))
    # Retain the previous spatial sampling; fix only the camera pole flip.
    offset = height * math.tan(math.radians(span / 2.)) * np.sin(angles) / math.sin(math.radians(span / 2.))
    points = np.column_stack((offset, np.zeros(count), np.full(count, height)))
    positions = _rotate_xy(points, base_yaw) + target
    pitches = -90. - np.rad2deg(np.arctan2(offset, height))
    rotations = np.column_stack((np.zeros(count), pitches, np.full(count, base_yaw)))
    return _reference(positions, rotations, target, height, base_yaw, count // 2)


def _arc_length_parameters(evaluate, count):
    dense_t = np.linspace(0., 1., 4097)
    dense = evaluate(dense_t)
    distance = np.concatenate(([0.], np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))))
    if distance[-1] <= 1e-8:
        raise ValueError('Trajectory must contain distinct viewpoints')
    return np.interp(np.linspace(0., distance[-1], count), distance, dense_t)


def spiral(target, count, radius, span, turns, base_yaw):
    _validate(count, radius, span)
    if not math.isfinite(turns) or turns <= 0:
        raise ValueError('Spiral turns must be finite and positive')
    half_angle = math.radians(span / 2.)
    def evaluate(t):
        polar = half_angle * (1. - np.asarray(t))
        azimuth = 2. * math.pi * turns * np.asarray(t)
        return radius * np.column_stack((np.sin(polar) * np.cos(azimuth),
                                         np.sin(polar) * np.sin(azimuth), np.cos(polar)))
    # Resample the continuous spiral, rather than joining golden-angle jumps.
    positions = _rotate_xy(evaluate(_arc_length_parameters(evaluate, count)), base_yaw) + target
    rotations = target_rotations(positions, target, base_yaw)
    return _reference(positions, rotations, target, radius, base_yaw, count - 1)


def _clamped_cubic(controls, parameters):
    """Cubic B-spline with nonnegative partition-of-unity weights (convex hull)."""
    controls = np.asarray(controls, dtype=float)
    parameters = np.asarray(parameters, dtype=float)
    n = len(controls)
    knots = np.r_[np.zeros(4), np.linspace(0., 1., n - 2)[1:-1], np.ones(4)]
    basis = np.column_stack([((parameters >= left) & (parameters < right)).astype(float)
                             for left, right in zip(knots[:-1], knots[1:])])
    for degree in range(1, 4):
        next_basis = np.zeros((len(parameters), basis.shape[1] - 1))
        for i in range(next_basis.shape[1]):
            first = knots[i + degree] - knots[i]
            second = knots[i + degree + 1] - knots[i + 1]
            if first > 0:
                next_basis[:, i] += (parameters - knots[i]) / first * basis[:, i]
            if second > 0:
                next_basis[:, i] += (knots[i + degree + 1] - parameters) / second * basis[:, i + 1]
        basis = next_basis
    result = basis @ controls
    result[parameters == 1.] = controls[-1]
    result[parameters == 0.] = controls[0]
    return result


def random_shell(target, count, minimum, maximum, span, height, base_yaw, rng=None):
    _validate(count, height, span)
    if not np.isfinite([minimum, maximum]).all() or not 0 < minimum <= height <= maximum:
        raise ValueError('Reference height must lie inside the positive radial bounds')
    rng = random if rng is None else rng
    half_angle = math.radians(span / 2.)
    controls = []
    # Interpolate radius and a tilt vector within a convex disk. Cartesian
    # Catmull-Rom control points do not constrain the interpolated shell/cap.
    margin = .05 * (maximum - minimum)
    for _ in range(6):
        radius = rng.uniform(minimum + margin, maximum)
        polar = math.acos(rng.uniform(math.cos(half_angle), 1.))
        azimuth = rng.uniform(0., 2. * math.pi)
        controls.append((radius, polar * math.cos(azimuth), polar * math.sin(azimuth)))
    # The reference is a curve endpoint with a zero endpoint derivative, not
    # an extra camera pose appended after an unrelated random last point.
    controls.extend([(height, 0., 0.), (height, 0., 0.)])
    def evaluate(t):
        values = _clamped_cubic(controls, t)
        radius, tilt = values[:, 0], values[:, 1:]
        polar = np.linalg.norm(tilt, axis=1)
        # np.sinc(x/pi)=sin(x)/x is well defined at the zenith.
        horizontal = tilt * np.sinc(polar / math.pi)[:, None]
        return radius[:, None] * np.column_stack((horizontal, np.cos(polar)))
    positions = _rotate_xy(evaluate(_arc_length_parameters(evaluate, count)), base_yaw) + target
    rotations = target_rotations(positions, target, base_yaw)
    return _reference(positions, rotations, target, height, base_yaw, count - 1)
