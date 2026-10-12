import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { Expand, Grid2X2, RotateCcw, Box, Layers3 } from "lucide-react";
import { headers } from "./api";

type Props = {
  jobId?: string;
  modelHash?: string | null;
  buildVolume: number[];
  busy: boolean;
};

export function Viewer({ jobId, modelHash, buildVolume, busy }: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const actions = useRef<{
    reset: () => void;
    grid: () => void;
    wire: () => void;
  } | null>(null);
  const [error, setError] = useState("");
  const [showGrid, setShowGrid] = useState(true);
  const [wire, setWire] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [width, depth] = buildVolume;

  useEffect(() => {
    const element = mount.current;
    if (!element) return;
    setError("");
    setLoaded(false);
    setShowGrid(true);
    setWire(false);
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setError("3D preview requires WebGL. You can still download the model.");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0xf1f0eb, 1);
    element.appendChild(renderer.domElement);
    renderer.domElement.setAttribute(
      "aria-label",
      "Interactive 3D model. Drag to orbit, scroll to zoom, right-drag to pan.",
    );
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 5000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI / 2.02;
    controls.minDistance = 10;
    controls.maxDistance = 1500;
    scene.add(new THREE.HemisphereLight(0xffffff, 0xa39c8f, 3));
    const key = new THREE.DirectionalLight(0xffffff, 4);
    key.position.set(-100, 250, 120);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -200;
    key.shadow.camera.right = 200;
    key.shadow.camera.top = 200;
    key.shadow.camera.bottom = -200;
    key.shadow.bias = -0.0004;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffd9c2, 1.3);
    fill.position.set(150, 70, -100);
    scene.add(fill);
    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(width, 2, depth),
      new THREE.MeshStandardMaterial({ color: 0xe7e6e0, roughness: 0.9 }),
    );
    plate.position.y = -1.3;
    plate.receiveShadow = true;
    scene.add(plate);
    const grid = new THREE.GridHelper(
      Math.max(width, depth),
      Math.round(Math.max(width, depth) / 10),
      0xc4c3bb,
      0xd6d5cd,
    );
    grid.position.y = -0.2;
    scene.add(grid);
    const border = new THREE.LineSegments(
      new THREE.EdgesGeometry(plate.geometry),
      new THREE.LineBasicMaterial({ color: 0xc4c3ba }),
    );
    border.position.copy(plate.position);
    scene.add(border);
    const material = new THREE.MeshStandardMaterial({
      color: 0xdd7652,
      roughness: 0.42,
      metalness: 0.05,
    });
    let target = new THREE.Vector3(0, 12, 0);
    let distance = Math.max(width, depth) * 1.25;
    function reset() {
      camera.position
        .copy(target)
        .add(new THREE.Vector3(distance * 0.9, distance * 0.7, distance));
      controls.target.copy(target);
      controls.update();
    }
    reset();
    actions.current = {
      reset,
      grid: () => {
        grid.visible = !grid.visible;
      },
      wire: () => {
        material.wireframe = !material.wireframe;
      },
    };
    const abort = new AbortController();
    if (jobId && modelHash) {
      fetch(`/api/jobs/${jobId}/files/model.stl`, {
        headers: headers(),
        signal: abort.signal,
      })
        .then((response) => {
          if (!response.ok) throw new Error("Unable to load the model");
          return response.arrayBuffer();
        })
        .then((buffer) => {
          if (abort.signal.aborted) return;
          const geometry = new STLLoader().parse(buffer);
          geometry.rotateX(-Math.PI / 2);
          geometry.computeVertexNormals();
          const mesh = new THREE.Mesh(geometry, material);
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          scene.add(mesh);
          const bounds = new THREE.Box3().setFromObject(mesh);
          target = bounds.getCenter(new THREE.Vector3());
          const size = bounds.getSize(new THREE.Vector3());
          distance = Math.max(size.x, size.y, size.z, 45) * 1.6;
          reset();
          setLoaded(true);
        })
        .catch((err: Error) => {
          if (err.name !== "AbortError") setError(err.message);
        });
    }
    const resize = new ResizeObserver(() => {
      const w = element.clientWidth,
        h = element.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    });
    resize.observe(element);
    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });
    return () => {
      abort.abort();
      resize.disconnect();
      controls.dispose();
      renderer.setAnimationLoop(null);
      scene.traverse((object) => {
        if (
          object instanceof THREE.Mesh ||
          object instanceof THREE.LineSegments
        ) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material)
            ? object.material
            : [object.material];
          materials.forEach((m) => m.dispose());
        }
      });
      material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      actions.current = null;
    };
  }, [jobId, modelHash, width, depth]);

  return (
    <div className="viewer-shell">
      <div className="viewer-canvas" ref={mount} />
      <div className="viewer-top">
        <span className="viewer-label">
          <span className={`dot ${loaded ? "green" : ""}`} />
          {loaded
            ? "Solid model"
            : busy
              ? "Creating your model"
              : "Your build plate"}
        </span>
        <span className="unit-label">MILLIMETERS</span>
      </div>
      {!modelHash && !busy && (
        <div className="viewer-empty">
          <div className="empty-symbol">
            <Box size={35} strokeWidth={1} />
          </div>
          <h2>
            A little idea.
            <br />A real-world thing.
          </h2>
          <p>
            Describe what you have in mind.
            <br />
            Your next creation starts here.
          </p>
        </div>
      )}
      {busy && !loaded && (
        <div className="viewer-empty">
          <div className="building-orbit">
            <Box size={36} strokeWidth={1.4} />
          </div>
          <h2>Giving your idea shape.</h2>
          <p>Building a real, editable CAD solid.</p>
        </div>
      )}
      {error && (
        <div role="alert" className="viewer-error">
          {error}
        </div>
      )}
      <div className="viewer-bottom">
        <span className="plate-label">
          A1 MINI <span> / </span>
          {width} × {depth} mm
        </span>
        <div className="viewer-tools">
          <button
            title="Reset view"
            aria-label="Reset view"
            onClick={() => actions.current?.reset()}
          >
            <RotateCcw size={16} />
          </button>
          <button
            title="Toggle grid"
            aria-label="Toggle grid"
            aria-pressed={showGrid}
            onClick={() => {
              actions.current?.grid();
              setShowGrid(!showGrid);
            }}
          >
            <Grid2X2 size={16} />
          </button>
          <button
            title="Toggle wireframe"
            aria-label="Toggle wireframe"
            aria-pressed={wire}
            onClick={() => {
              actions.current?.wire();
              setWire(!wire);
            }}
          >
            <Layers3 size={16} />
          </button>
          <button
            title="Fullscreen preview"
            aria-label="Fullscreen preview"
            onClick={() => {
              void mount.current?.parentElement
                ?.requestFullscreen()
                .catch(() =>
                  setError("Fullscreen is unavailable in this browser"),
                );
            }}
          >
            <Expand size={16} />
          </button>
        </div>
      </div>
      <div className="orbit-hint">
        Drag to orbit <span>·</span> Scroll to zoom <span>·</span> Right-drag to
        pan
      </div>
    </div>
  );
}
