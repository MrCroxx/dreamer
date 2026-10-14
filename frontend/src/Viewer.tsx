import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";
import { Expand, Grid2X2, RotateCcw, Box, Layers3 } from "lucide-react";
import { headers } from "./api";

type Props = {
  jobId?: string;
  modelHash?: string | null;
  buildVolume: number[];
  busy: boolean;
  live?: boolean;
  color?: string;
  onDraftChange?: (visible: boolean) => void;
};

export function Viewer({
  jobId,
  modelHash,
  buildVolume,
  busy,
  live = false,
  color = "#dd7652",
  onDraftChange,
}: Props) {
  const mount = useRef<HTMLDivElement>(null);
  const updateMesh = useRef<((buffer: ArrayBuffer | null) => void) | null>(
    null,
  );
  const [draftStep, setDraftStep] = useState<number | null>(null);
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
  const modelMaterial = useRef<THREE.MeshStandardMaterial | null>(null);

  useEffect(() => {
    onDraftChange?.(loaded && draftStep !== null);
  }, [loaded, draftStep, onDraftChange]);

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
    scene.add(new THREE.HemisphereLight(0xffffff, 0xa39c8f, 1.8));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(-100, 250, 120);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -200;
    key.shadow.camera.right = 200;
    key.shadow.camera.top = 200;
    key.shadow.camera.bottom = -200;
    key.shadow.bias = -0.0004;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.8);
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
      color,
      roughness: 0.6,
      metalness: 0,
    });
    modelMaterial.current = material;
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
    let mesh: THREE.Mesh | null = null;
    let fitted = false;
    updateMesh.current = (buffer) => {
      if (mesh) {
        scene.remove(mesh);
        mesh.geometry.dispose();
        mesh = null;
      }
      if (!buffer) {
        setLoaded(false);
        return;
      }
      const geometry = new STLLoader().parse(buffer);
      geometry.rotateX(-Math.PI / 2);
      // Smooth tessellated curves while keeping sharp CAD edges in the preview.
      if (geometry.getAttribute("position").count <= 300_000)
        toCreasedNormals(geometry);
      else geometry.computeVertexNormals();
      mesh = new THREE.Mesh(geometry, material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
      const bounds = new THREE.Box3().setFromObject(mesh);
      target = bounds.getCenter(new THREE.Vector3());
      const size = bounds.getSize(new THREE.Vector3());
      distance = Math.max(size.x, size.y, size.z, 45) * 1.6;
      if (!fitted) {
        reset();
        fitted = true;
      }
      setLoaded(true);
    };
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
      updateMesh.current = null;
      modelMaterial.current = null;
    };
  }, [jobId, width, depth]);

  useEffect(() => {
    modelMaterial.current?.color.set(color);
  }, [color, jobId, width, depth]);

  useEffect(() => {
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let revision: string | null = null;
    setError("");
    if (!modelHash) setDraftStep(null);
    async function load(path: string) {
      const response = await fetch(path, {
        headers: headers(),
        signal: abort.signal,
        cache: "no-store",
      });
      if (!response.ok) throw new Error("Unable to load the model");
      const buffer = await response.arrayBuffer();
      if (!abort.signal.aborted) updateMesh.current?.(buffer);
    }
    async function poll() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/preview`, {
          headers: headers(),
          signal: abort.signal,
          cache: "no-store",
        });
        if (!response.ok)
          throw new Error(
            "Live preview is temporarily unavailable. The final model will appear when ready.",
          );
        const data = (await response.json()) as {
          active: boolean;
          preview: { revision: string; operations: number } | null;
        };
        if (abort.signal.aborted) return;
        if (data.preview && data.preview.revision !== revision) {
          await load(`/api/jobs/${jobId}/preview/${data.preview.revision}`);
          if (abort.signal.aborted) return;
          revision = data.preview.revision;
          setDraftStep(data.preview.operations);
        } else if (!data.preview && data.active && revision) {
          updateMesh.current?.(null);
          revision = null;
          setDraftStep(null);
        }
        setError("");
      } catch (err) {
        if (!abort.signal.aborted) setError((err as Error).message);
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 1000);
      }
    }
    if (jobId && modelHash) {
      void load(`/api/jobs/${jobId}/files/model.stl`)
        .then(() => {
          if (!abort.signal.aborted) setDraftStep(null);
        })
        .catch((err: Error) => {
          if (!abort.signal.aborted) setError(err.message);
        });
    } else if (jobId && live) {
      void poll();
    } else {
      updateMesh.current?.(null);
    }
    return () => {
      abort.abort();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, modelHash, live, width, depth]);

  return (
    <div className="viewer-shell">
      <div className="viewer-canvas" ref={mount} data-color={color} />
      <div className="viewer-top">
        <span className="viewer-label">
          <span className={`dot ${loaded ? "green" : ""}`} />
          {loaded
            ? draftStep !== null
              ? `Live draft · Step ${draftStep}`
              : "Solid model"
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
          <p>Completed steps will appear here as the model takes shape.</p>
        </div>
      )}
      {error && (
        <div role="alert" className="viewer-error">
          {error}
        </div>
      )}
      {draftStep !== null && loaded && (
        <div className="draft-notice" role="status">
          Live draft · Final geometry is still being validated
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
