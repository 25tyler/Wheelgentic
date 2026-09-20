# Dual OpenYAM model provenance

This package contains one immutable dual-arm URDF. It is formed from two
namespaced copies of the verified DimOS single-arm OpenYAM model under one
geometry-free shared root. Runtime conversion logic is intentionally not
retained.

## Arm model authority

- Repository: <https://github.com/i2rt-robotics/i2rt>
- Commit: `5d47b358bafb30c65e397f2ece506550a0db4594`
- Source path: `i2rt/robot_models/arm/yam`
- DimOS packaged source: `yam_description/i2rt/yam.urdf`

Each side preserves the source arm's link transforms, inertials, joint axes,
joint limits, gripper geometry, and grasp-frame transform. The four finger
joints are fixed at the source model's zero position because they are driven by
two normalized hardware gripper commands rather than independent kinematic
coordinates. The twelve arm-joint velocity limits are set to 2 rad/s for the
teleoperation controller.

The required I2RT mesh assets are checked into this package so the dual model
is self-contained and does not fetch `yam_description` at runtime. They remain
the unchanged upstream mesh files covered by `I2RT_YAM_LICENSE`.

## ABC spacing authority

- Repository: <https://github.com/amazon-far/abc>
- Commit: `6bc6586721cf0c409ccee80f675a28de9b9b2f5e`
- Scene: `assets/put_bottles/put_bottle.xml`

Only the 0.62 m lateral distance between arm bases is retained from ABC. The
geometry-free `dual_openyam_base` root places the left base at `(0, +0.31, 0)`
and the right base at `(0, -0.31, 0)`, with identical orientation.

The ABC workbench, cabinet, enclosure, gate, cameras, task objects, and all
other environment or simulator content are deliberately excluded.

The ABC source is Apache-2.0 licensed. The packaged I2RT YAM source retains its
upstream license notice.
