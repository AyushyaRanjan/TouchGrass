import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { SettingsProvider } from "@/hooks/use-settings";
import { resetCameraFacingForLaunch } from "@/lib/camera-facing";
import { startUsageTracker } from "@/lib/usage";
import { SyncProvider } from "@/sync/provider";

export default function RootLayout() {
  useEffect(() => {
    void resetCameraFacingForLaunch();
    return startUsageTracker();
  }, []);

  return (
    <SafeAreaProvider>
      <SettingsProvider>
        <SyncProvider>
          <StatusBar style="dark" />
          <Stack screenOptions={{ headerShown: false }}>
            <Stack.Screen name="(tabs)" />
            <Stack.Screen name="capture" options={{ presentation: "fullScreenModal" }} />
          </Stack>
        </SyncProvider>
      </SettingsProvider>
    </SafeAreaProvider>
  );
}