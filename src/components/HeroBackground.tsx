import { memo } from "react";
import Globe from "./originkit/Globe";

// Decorative background only; aircraft data and playback stay in AirspaceView.
// Memo keeps feed refreshes and replay updates from rebuilding the renderer.
const HeroBackground = memo(function HeroBackground() {
  return (
    <Globe
      speed={0.4}
      oceanColor="#0a2928"
      outlineColor="#a0f0d0"
      graticuleColor="#234c49"
      dots={{ color: "#effff9", size: 4, density: 8, allDots: false }}
      initialLatitude={30}
      initialLongitude={30}
      scale={10}
      stopOnHover={false}
    />
  );
});

export default HeroBackground;
