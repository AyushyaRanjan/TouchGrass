/**
 * Proof capture: live camera -> preview -> resize -> save to the phone ->
 * queue. The sync engine uploads it whenever the server is reachable.
 * LIVE CAPTURE ONLY: there is deliberately no gallery / image picker here.
 */

import { Ionicons } from "@expo/vector-icons";
import { CameraView, useCameraPermissions } from "expo-camera";
import * as FileSystem from "expo-file-system/legacy";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import * as Location from "expo-location";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { AuthedImage } from "@/components/authed-image";
import { ResultCard } from "@/components/result-card";
import { BigButton, Card, Screen, TextAction } from "@/components/ui";
import { getPending, insertPending, subscribe, type PendingSubmission } from "@/db/local";
import { useSettings } from "@/hooks/use-settings";
import { loadCameraFacing, saveCameraFacing, type CameraFacing } from "@/lib/camera-facing";
import { dismiss } from "@/lib/dismissed";
import { uuidV4 } from "@/lib/uuid";
import { useSync } from "@/sync/provider";
import { colors, radius, spacing, type } from "@/theme";

const MAX_WIDTH = 1280;
const JPEG_QUALITY = 0.8;
const LOCATION_WAIT_MS = 1500;

type Shot = { uri: string; width: number; capturedAt: string; lat: number | null; lon: number | null };
type Phase =
  | { name: "camera" }
  | { name: "preview"; shot: Shot }
  | { name: "saving" }
  | { name: "saved"; id: string }
  | { name: "error"; message: string };

export default function CaptureScreen() {
  const router = useRouter();
  const { configured } = useSettings();
  const { syncNow } = useSync();
  const params = useLocalSearchParams<{ taskId: string; title?: string; notePrompt?: string }>();
  const taskId = Number(params.taskId);

  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const [phase, setPhase] = useState<Phase>({ name: "camera" });
  const [note, setNote] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [facing, setFacing] = useState<CameraFacing>("back");
  const [locationGranted, setLocationGranted] = useState(false);
  const [item, setItem] = useState<PendingSubmission | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Restore the lens chosen earlier in this session (launch reset is in _layout).
  useEffect(() => {
    void loadCameraFacing().then((value) => {
      if (alive.current) setFacing(value);
    });
  }, []);

  const flipCamera = () => {
    if (capturing) return;
    const next: CameraFacing = facing === "back" ? "front" : "back";
    setFacing(next);
    void saveCameraFacing(next);
  };

  // Ask for location once, quietly. Never blocks anything.
  useEffect(() => {
    Location.requestForegroundPermissionsAsync()
      .then((res) => alive.current && setLocationGranted(res.granted))
      .catch(() => undefined);
  }, []);

  // Once saved, watch our queue row so the verdict appears here when it arrives.
  const savedId = phase.name === "saved" ? phase.id : null;
  useEffect(() => {
    if (!savedId) return;
    const refresh = () =>
      void getPending(savedId).then((row) => {
        if (alive.current) setItem(row);
      });
    refresh();
    return subscribe(refresh);
  }, [savedId]);

  const readLocation = useCallback(async (): Promise<{ lat: number | null; lon: number | null }> => {
    if (!locationGranted) return { lat: null, lon: null };
    try {
      const pos = await Promise.race([
        Location.getLastKnownPositionAsync(),
        new Promise<null>((resolve) => setTimeout(() => resolve(null), LOCATION_WAIT_MS)),
      ]);
      return pos ? { lat: pos.coords.latitude, lon: pos.coords.longitude } : { lat: null, lon: null };
    } catch {
      return { lat: null, lon: null };
    }
  }, [locationGranted]);

  const takePhoto = async () => {
    if (!cameraRef.current || capturing) return;
    setCapturing(true);
    try {
      const capturedAt = new Date().toISOString();
      const picture = await cameraRef.current.takePictureAsync({ quality: 0.9 });
      const where = await readLocation();
      setPhase({
        name: "preview",
        shot: { uri: picture.uri, width: picture.width, capturedAt, ...where },
      });
    } catch (err) {
      Alert.alert("Couldn't take the photo", err instanceof Error ? err.message : "Please try again.");
    } finally {
      setCapturing(false);
    }
  };

  /** Resize to max 1280px wide, JPEG 0.8, then copy to documentDirectory so it survives cache clearing. */
  const savePhoto = async (shot: Shot, clientSubmissionId: string): Promise<string> => {
    const ctx = ImageManipulator.manipulate(shot.uri);
    if (shot.width > MAX_WIDTH) ctx.resize({ width: MAX_WIDTH });
    const ref = await ctx.renderAsync();
    const saved = await ref.saveAsync({ compress: JPEG_QUALITY, format: SaveFormat.JPEG });
    if (!FileSystem.documentDirectory) throw new Error("No permanent storage available on this device.");
    const dir = `${FileSystem.documentDirectory}photos/`;
    await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => undefined);
    const target = `${dir}${clientSubmissionId}.jpg`;
    await FileSystem.copyAsync({ from: saved.uri, to: target });
    return target;
  };

  const submitShot = async (shot: Shot) => {
    setPhase({ name: "saving" });
    try {
      const id = uuidV4();
      const imageUri = await savePhoto(shot, id);
      await insertPending({
        client_submission_id: id,
        task_id: taskId,
        image_uri: imageUri,
        captured_at: shot.capturedAt,
        lat: shot.lat,
        lon: shot.lon,
        note: note.trim() || null,
      });
      if (!alive.current) return;
      setPhase({ name: "saved", id });
      void syncNow(true);
    } catch (err) {
      console.warn("saving photo failed", err instanceof Error ? `${err.name}: ${err.message}` : String(err));
      if (!alive.current) return;
      setPhase({ name: "error", message: "The app couldn't prepare the photo" });
    }
  };

  const retake = () => {
    setNote("");
    setItem(null);
    setPhase({ name: "camera" });
  };
  const close = () => router.back();

  // --- permission ------------------------------------------------------------
  if (!permission) return <View style={styles.black} />;
  if (!permission.granted && phase.name === "camera") {
    return (
      <Screen>
        <Card>
          <Ionicons name="camera-outline" size={32} color={colors.accent} />
          <Text style={[type.heading, { marginTop: spacing.md }]}>Camera, please</Text>
          <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs }]}>
            Touch Grass needs the camera to take a live proof photo. Photos only go to your own server.
          </Text>
        </Card>
        {permission.canAskAgain ? (
          <BigButton label="Allow camera" onPress={() => void requestPermission()} />
        ) : (
          <BigButton label="Open settings" onPress={() => void Linking.openSettings()} />
        )}
        <TextAction label="Not now" onPress={close} />
      </Screen>
    );
  }

  // --- phases ----------------------------------------------------------------
  if (phase.name === "camera") {
    return (
      <View style={styles.black}>
        <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing={facing} mirror={false} />
        <Pressable style={styles.closeButton} onPress={close} accessibilityLabel="Close camera">
          <Ionicons name="close" size={26} color="#fff" />
        </Pressable>
        <View style={styles.shutterWrap}>
          {params.title ? <Text style={styles.cameraTitle}>{params.title}</Text> : null}
          <Pressable
            onPress={() => void takePhoto()}
            disabled={capturing}
            accessibilityLabel="Take photo"
            style={[styles.shutterOuter, capturing && { opacity: 0.5 }]}
          >
            <View style={styles.shutterInner} />
          </Pressable>
        </View>
        <Pressable
          style={[styles.flipButton, capturing && { opacity: 0.5 }]}
          onPress={flipCamera}
          disabled={capturing}
          accessibilityLabel="Flip camera"
        >
          <Ionicons name="camera-reverse-outline" size={26} color="#fff" />
        </Pressable>
      </View>
    );
  }

  if (phase.name === "preview") {
    const shot = phase.shot;
    return (
      <Screen>
        <AuthedImage
          submissionId={null}
          thumb={false}
          localUri={shot.uri}
          style={styles.preview}
          placeholderStyle={styles.preview}
          resizeMode="cover"
        />
        {params.notePrompt ? (
          <TextInput
            style={[type.body, styles.note]}
            placeholder={params.notePrompt}
            placeholderTextColor={colors.neutral}
            value={note}
            onChangeText={setNote}
            multiline
          />
        ) : null}
        <BigButton label="Use this photo" onPress={() => void submitShot(shot)} />
        <TextAction label="Retake" onPress={retake} />
      </Screen>
    );
  }

  if (phase.name === "saving") {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.accent} />
        <Text style={[type.heading, { marginTop: spacing.lg }]}>Saving your photo...</Text>
      </View>
    );
  }

  if (phase.name === "error") {
    return (
      <Screen>
        <Card>
          <Text style={type.heading}>Couldn&apos;t save that</Text>
          <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs }]}>{phase.message}</Text>
        </Card>
        <BigButton label="Retake" onPress={retake} />
        <TextAction label="Close" onPress={close} />
      </Screen>
    );
  }

  // saved: show the verdict as soon as the engine has it, otherwise a calm waiting note
  if (item && (item.status === "done" || item.status === "failed")) {
    // Dismiss first (synchronous) so Today does not re-open this card.
    const id = item.client_submission_id;
    return (
      <ResultCard
        item={item}
        onRetake={() => {
          dismiss(id);
          retake();
        }}
        onDone={() => {
          dismiss(id);
          close();
        }}
      />
    );
  }

  const sending = item?.status === "uploading" || item?.status === "uploaded";
  return (
    <View style={styles.centered}>
      {sending ? (
        <ActivityIndicator size="large" color={colors.accent} />
      ) : (
        <Ionicons name="checkmark-circle-outline" size={44} color={colors.accent} />
      )}
      <Text style={[type.heading, { marginTop: spacing.lg, textAlign: "center" }]}>
        {sending ? "Checking your photo..." : "Saved."}
      </Text>
      <Text style={[type.body, styles.mutedCenter]}>
        {sending
          ? "This can take a minute. Feel free to put your phone away; we will keep checking."
          : configured
            ? "I'll check it when I can reach your laptop."
            : "Add your server in Settings so I can check it."}
      </Text>
      <View style={{ marginTop: spacing.xl, alignSelf: "stretch" }}>
        <BigButton label="Back to Today" onPress={close} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  black: { flex: 1, backgroundColor: "#000" },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.xl,
    backgroundColor: colors.background,
  },
  mutedCenter: { color: colors.textMuted, textAlign: "center", marginTop: spacing.sm },
  closeButton: {
    position: "absolute",
    top: 56,
    left: spacing.lg,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.4)",
    alignItems: "center",
    justifyContent: "center",
  },
  flipButton: {
    position: "absolute",
    bottom: 48,
    right: spacing.lg,
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "rgba(0,0,0,0.4)",
    alignItems: "center",
    justifyContent: "center",
  },
  shutterWrap: { position: "absolute", bottom: 48, width: "100%", alignItems: "center", gap: spacing.md },
  cameraTitle: { color: "#fff", fontSize: 15, fontWeight: "600", paddingHorizontal: spacing.xl, textAlign: "center" },
  shutterOuter: {
    width: 78,
    height: 78,
    borderRadius: 39,
    borderWidth: 4,
    borderColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
  },
  shutterInner: { width: 58, height: 58, borderRadius: 29, backgroundColor: "#fff" },
  preview: { width: "100%", aspectRatio: 3 / 4, borderRadius: radius.md, backgroundColor: colors.neutralSoft },
  note: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    padding: spacing.md,
    minHeight: 72,
    color: colors.text,
  },
});
