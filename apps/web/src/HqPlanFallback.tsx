/** Building floor plan for browsers without WebGL: the 2D canvas renderer with the same saved rooms. */
import { useState } from 'react';
import { HeadquartersCanvas } from './HeadquartersCanvas';
import { initialCamera, type CameraState } from './lib/headquartersSpatial.ts';
import type { RoomGeometry, SceneFloor } from './lib/headquartersProjection.ts';

export function HqPlanFallback(props: Readonly<{
  floor: SceneFloor; selectedRoomId: string | null; night: boolean; onSelectRoom: (room: RoomGeometry | null) => void;
}>) {
  const [camera, setCamera] = useState<CameraState>(() => initialCamera(props.floor));
  const [floorId, setFloorId] = useState(props.floor.id);
  if (floorId !== props.floor.id) { setFloorId(props.floor.id); setCamera(initialCamera(props.floor)); }
  return (
    <div className="hqPlanStage">
      <HeadquartersCanvas
        floor={props.floor} selectedRoomId={props.selectedRoomId} camera={camera} setCamera={setCamera} night={props.night}
        onSelectRoom={(id) => props.onSelectRoom(props.floor.rooms.find((r) => r.id === id) ?? null)}
      />
    </div>
  );
}
