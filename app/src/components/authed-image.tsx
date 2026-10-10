/** An <Image> for photos that may be on the server or still on the phone, always via useAuthedImage. */

import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Image, View } from "react-native";
import type { ImageStyle, StyleProp, ViewStyle } from "react-native";

import { useAuthedImage } from "@/hooks/use-authed-image";
import { colors } from "@/theme";

export function AuthedImage({
  submissionId,
  thumb,
  localUri,
  style,
  placeholderStyle,
  resizeMode,
}: {
  submissionId: number | null;
  thumb: boolean;
  localUri?: string | null;
  style: StyleProp<ImageStyle>;
  placeholderStyle: StyleProp<ViewStyle>;
  resizeMode?: "cover" | "contain";
}) {
  const uri = useAuthedImage(submissionId, thumb, localUri);
  if (!uri) return <Placeholder style={placeholderStyle} />;
  return <LoadedImage key={uri} uri={uri} style={style} placeholderStyle={placeholderStyle} resizeMode={resizeMode} />;
}

function LoadedImage({
  uri,
  style,
  placeholderStyle,
  resizeMode,
}: {
  uri: string;
  style: StyleProp<ImageStyle>;
  placeholderStyle: StyleProp<ViewStyle>;
  resizeMode?: "cover" | "contain";
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Placeholder style={placeholderStyle} />;
  return <Image source={{ uri }} style={style} resizeMode={resizeMode} onError={() => setFailed(true)} />;
}

function Placeholder({ style }: { style: StyleProp<ViewStyle> }) {
  return (
    <View style={[{ alignItems: "center", justifyContent: "center", backgroundColor: colors.neutralSoft }, style]}>
      <Ionicons name="image-outline" size={22} color={colors.neutral} />
    </View>
  );
}
