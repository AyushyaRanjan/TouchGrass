/**
 * Front/back camera choice. Stored in AsyncStorage for this JS session
 * (so leaving the camera and coming back keeps the last lens) and forced
 * back to "back" once per app launch.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";

export const CAMERA_FACING_KEY = "tg:session:cameraFacing";
export type CameraFacing = "back" | "front";

// First write of this JS runtime: always back. Later saves in this session win.
let queue: Promise<void> = AsyncStorage.setItem(CAMERA_FACING_KEY, "back");

/** Idempotent: the launch reset already started when this module loaded. */
export async function resetCameraFacingForLaunch(): Promise<void> {
  await queue;
}

export async function loadCameraFacing(): Promise<CameraFacing> {
  await queue;
  const raw = await AsyncStorage.getItem(CAMERA_FACING_KEY);
  return raw === "front" ? "front" : "back";
}

export async function saveCameraFacing(facing: CameraFacing): Promise<void> {
  queue = queue.then(() => AsyncStorage.setItem(CAMERA_FACING_KEY, facing));
  await queue;
}
