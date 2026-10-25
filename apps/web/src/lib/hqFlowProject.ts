/** Projects a scene point to percentages of the stage box, for HTML markers laid over the 3D
 * view. Same camera maths as the renderer (headquartersSpatial.ts); no state, no effects. */
import { cameraBasis, cameraEye, dot, vsub, type CameraState } from './headquartersSpatial.ts';
import type { RoomGeometry, Vec3 } from './headquartersProjection.ts';

export interface StagePoint { left: number; top: number }

export function projectToStage(camera: CameraState, point: Vec3, aspect: number): StagePoint | null {
  const basis = cameraBasis(camera);
  const relative = vsub(point, cameraEye(camera));
  const depth = dot(relative, basis.forward);
  if (depth <= 0.005) return null;
  const scale = 1 / Math.tan((camera.fov * Math.PI) / 360);
  const x = (dot(relative, basis.right) * scale) / depth / aspect;
  const y = (dot(relative, basis.up) * scale) / depth;
  if (Math.abs(x) > 1.05 || Math.abs(y) > 1.05) return null;
  return { left: (x + 1) * 50, top: (1 - y) * 50 };
}

/** Where a room's flow badge sits: low on the room, so it does not cover the room label. */
export function roomBadgePoint(room: RoomGeometry): Vec3 {
  return { x: room.x + room.width * 0.5, y: room.height * 0.12, z: room.z + room.depth * 0.78 };
}
