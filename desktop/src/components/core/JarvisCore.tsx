interface JarvisOrbProps {
  size?: number;
  active?: boolean; // true = listening/speaking, pulses faster and brighter
}

export function JarvisOrb({ size = 96, active = false }: JarvisOrbProps) {
  return (
    <div
      className="orb"
      style={{
        width: size,
        height: size,
        filter: active ? "brightness(1.3)" : undefined,
      }}
    >
      <div className="orb-ring orb-ring-1" style={{ inset: -14 }} />
      <div className="orb-ring orb-ring-2" style={{ inset: -26 }} />
      <div
        className="orb-core"
        style={{
          width: size * 0.28,
          height: size * 0.28,
          animationDuration: active ? "0.9s" : "2.4s",
        }}
      />
    </div>
  );
}
