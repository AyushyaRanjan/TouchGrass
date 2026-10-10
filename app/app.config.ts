import type { ExpoConfig } from "expo/config";

const config: ExpoConfig = {
  name: "Touch Grass",
  slug: "touchgrass",
  version: "1.0.0",
  orientation: "portrait",
  icon: "./assets/images/icon.png",
  scheme: "touchgrass",
  userInterfaceStyle: "light",
  ios: {
    icon: "./assets/expo.icon",
    bundleIdentifier: "com.dagger.touchgrass",
    infoPlist: {
      // HTTP to the laptop is only over Tailscale (WireGuard), which is already
      // encrypted. This is not cleartext on the public internet.
      NSAppTransportSecurity: {
        NSAllowsArbitraryLoads: true,
      },
    },
  },
  android: {
    package: "com.dagger.touchgrass",
    adaptiveIcon: {
      backgroundColor: "#F2F8F2",
      foregroundImage: "./assets/images/android-icon-foreground.png",
      backgroundImage: "./assets/images/android-icon-background.png",
      monochromeImage: "./assets/images/android-icon-monochrome.png",
    },
    predictiveBackGestureEnabled: false,
  },
  web: {
    output: "static",
    favicon: "./assets/images/favicon.png",
  },
  plugins: [
    "expo-router",
    [
      "expo-splash-screen",
      {
        backgroundColor: "#F2F8F2",
        image: "./assets/images/splash-icon.png",
        imageWidth: 76,
      },
    ],
    "expo-font",
    [
      "expo-camera",
      {
        cameraPermission: "Touch Grass uses the camera to take a live proof photo of your task.",
        microphonePermission: false,
        recordAudioAndroid: false,
      },
    ],
    [
      "expo-location",
      {
        locationWhenInUsePermission:
          "Touch Grass can attach your location to a proof photo. This is optional.",
      },
    ],
    [
      "expo-build-properties",
      {
        android: {
          // Same Tailscale overlay as iOS ATS: HTTP to the laptop is already encrypted.
          usesCleartextTraffic: true,
        },
      },
    ],
  ],
  experiments: {
    typedRoutes: true,
    reactCompiler: true,
  },
  extra: {
    eas: {
      projectId: "91badedd-f1c3-4bbe-9ed3-6b2d2858d15a",
    },
  },
};

export default config;
