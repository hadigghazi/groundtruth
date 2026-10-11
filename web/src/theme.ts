// The GroundTruth theme for Fluent 2: a night-pitch studio lit by one floodlight colour.
//
// Fluent supplies the controls, their states and their accessibility. The brand ramp
// below replaces Fluent's blue; a few neutral tokens are tinted green-black so the app
// chrome sits in the same world as the pitch. The broadcast graphics in styles.css read
// the same values through the --gt-* variables.

import { createDarkTheme, type BrandVariants, type Theme } from "@fluentui/react-components";

export const FLOODLIGHT = "#c6ff3d"; // the brand colour: verified, live, primary actions
export const AMBER = "#ffb547"; // a template card, a fallback
export const CORAL = "#ff5d5d"; // a rejection, a miss

const brand: BrandVariants = {
  10: "#0b1000", 20: "#141f00", 30: "#1d2d00", 40: "#263c00", 50: "#304b00", 60: "#3a5b00",
  70: "#456b00", 80: "#507c00", 90: "#5c8d00", 100: "#699f00", 110: "#77b100", 120: "#86c400",
  130: "#96d700", 140: "#a8ea14", 150: "#c6ff3d", 160: "#dcff8a",
};

const base = createDarkTheme(brand);

export const gtTheme: Theme = {
  ...base,
  fontFamilyBase: "'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, 'Cairo Variable', 'Helvetica Neue', sans-serif",
  // a filled lime button with near-black text reads like a broadcast "on air" key
  colorBrandBackground: FLOODLIGHT,
  colorBrandBackgroundHover: "#d4ff66",
  colorBrandBackgroundPressed: "#a8ea14",
  colorBrandBackgroundSelected: "#b6f22c",
  colorNeutralForegroundOnBrand: "#0b1000",
  colorBrandForeground1: FLOODLIGHT,
  colorBrandForeground2: "#dcff8a",
  colorBrandForegroundLink: FLOODLIGHT,
  colorBrandForegroundLinkHover: "#dcff8a",
  colorCompoundBrandBackground: FLOODLIGHT,
  colorCompoundBrandBackgroundHover: "#d4ff66",
  colorCompoundBrandForeground1: FLOODLIGHT,
  colorCompoundBrandStroke: FLOODLIGHT,
  colorBrandStroke1: FLOODLIGHT,
  colorStrokeFocus2: FLOODLIGHT,
  // green-black neutrals
  colorNeutralBackground1: "#0e1513",
  colorNeutralBackground1Hover: "#15201c",
  colorNeutralBackground1Pressed: "#0a100e",
  colorNeutralBackground1Selected: "#18241f",
  colorNeutralBackground2: "#0b110f",
  colorNeutralBackground3: "#141d1a",
  colorNeutralBackground4: "#18231f",
  colorSubtleBackgroundHover: "#16211d",
  colorSubtleBackgroundPressed: "#0f1714",
  colorSubtleBackgroundSelected: "#1a2621",
  colorNeutralStroke1: "#2a3833",
  colorNeutralStroke2: "#22302b",
  colorNeutralStrokeAccessible: "#7d918a",
  colorNeutralForeground1: "#e9f1ee",
  colorNeutralForeground2: "#bfcdc8",
  colorNeutralForeground3: "#8fa39b",
  colorNeutralForeground4: "#6f827b",
};
