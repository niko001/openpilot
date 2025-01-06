using Cxx = import "./include/c++.capnp";
$Cxx.namespace("cereal");

@0xf98f2c53a2739e6e;

struct WazeAlerts {
  alertType @0 :Text;
  alertSubType @1 :Text;
  distance @2 :Float32;
  roadName @3 :Text;
  city @4 :Text;
}
