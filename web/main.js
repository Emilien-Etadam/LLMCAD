import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.173.0/+esm';
import CameraControls from 'https://cdn.jsdelivr.net/npm/camera-controls@2.9.0/+esm';
import { models } from './models.js';

CameraControls.install({ THREE });
const api = window.location.origin + '/api/';

const codeInput = document.getElementById('code-input');
if (codeInput && !codeInput.value) {
  codeInput.value = models['default'];
}

// initialize three.js viewer
const viewerContainer = document.getElementById('viewer');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(
  75,
  Math.max(viewerContainer.clientWidth, 1) / Math.max(viewerContainer.clientHeight, 1),
  0.1,
  1000
);
const renderer = new THREE.WebGLRenderer();
renderer.setClearColor(0xffffff);
scene.background = new THREE.Color(0xffffff);

renderer.setSize(
  Math.max(viewerContainer.clientWidth, 1),
  Math.max(viewerContainer.clientHeight, 1)
);
viewerContainer.appendChild(renderer.domElement);

const light = new THREE.DirectionalLight(0xffffff, 1);
light.position.set(1, 1, 1);
scene.add(light);
scene.add(new THREE.AmbientLight(0x404040));

let gridHelper = new THREE.GridHelper(10, 10);
scene.add(gridHelper);

camera.position.set(8, 8, 8);
camera.lookAt(0, 0, 0);
const cameraControls = new CameraControls(camera, renderer.domElement);

function getCSSVariable(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function cssColorToHex(color) {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.fillStyle = color;
  return parseInt(ctx.fillStyle.slice(1), 16);
}

function getMaterialProperties() {
  return {
    color: cssColorToHex(getCSSVariable('--material-color')),
    metalness: parseFloat(getCSSVariable('--material-metalness')),
    roughness: parseFloat(getCSSVariable('--material-roughness'))
  };
}

function rebuildGrid(model) {
  scene.remove(gridHelper);
  if (model) {
    const bbox = new THREE.Box3().setFromObject(model);
    const size = bbox.getSize(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.z) * 1.5;
    const gridSize = Math.max(10, Math.ceil(maxSize / 10) * 10);
    gridHelper = new THREE.GridHelper(gridSize, Math.floor(gridSize / 2));
  } else {
    gridHelper = new THREE.GridHelper(10, 10);
  }
  scene.add(gridHelper);
}

function updateOutput(message, success) {
  const outputContainer = document.getElementById('output-container');
  const outputMessage = document.getElementById('output-message');
  outputContainer.style.display = 'block';
  outputMessage.textContent = message;
  outputContainer.classList.remove('warning', 'success');
  if (success) {
    outputContainer.classList.add('success');
  } else {
    outputContainer.classList.add('warning');
  }
}

let currentModel = null;

/**
 * Send the textarea contents to /api/preview, render the resulting mesh,
 * and update the output panel. Returns {success, message}.
 */
export async function runPreview() {
  const preview_button = document.getElementById('preview-btn');
  preview_button.classList.add('button-disabled');
  updateOutput('Processing...', false);
  const code = document.getElementById('code-input').value;
  let result = { success: false, message: 'Unknown error' };
  try {
    const response = await fetch(api + 'preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    });
    const statusCode = response.status;
    const data = await response.json();
    const success = statusCode === 200 && data.message !== 'none';
    updateOutput(data.message, success);
    result = { success, message: data.message };

    if (success && data.data && data.data !== 'None') {
      if (currentModel) {
        scene.remove(currentModel);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.data.vertices, 3));
      geometry.setIndex(data.data.faces.flat());
      geometry.computeVertexNormals();
      const material = new THREE.MeshStandardMaterial(getMaterialProperties());
      currentModel = new THREE.Mesh(geometry, material);
      geometry.computeBoundingBox();
      const center = new THREE.Vector3();
      currentModel.geometry.boundingBox.getCenter(center);
      currentModel.geometry.translate(-center.x, 0, -center.z);
      scene.add(currentModel);
      rebuildGrid(currentModel);

      const bbox = new THREE.Box3().setFromObject(currentModel);
      const size = bbox.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);
      const fov = camera.fov * (Math.PI / 180);
      const cameraDistance = Math.abs(maxDim / Math.tan(fov / 2)) * 0.5;
      camera.position.set(cameraDistance, cameraDistance, cameraDistance);
      cameraControls.setLookAt(
        cameraDistance, cameraDistance, cameraDistance,
        0, 0, 0,
        true
      );
    }
  } catch (error) {
    console.log(error);
    const msg = error && error.message ? error.message : String(error);
    updateOutput('Error: ' + msg, false);
    result = { success: false, message: msg };
  }
  preview_button.classList.remove('button-disabled');
  return result;
}

/**
 * Drop the current mesh, reset the grid + camera to defaults, hide the
 * output panel.
 */
export function clearViewer() {
  if (currentModel) {
    scene.remove(currentModel);
    currentModel = null;
  }
  rebuildGrid(null);
  cameraControls.setLookAt(8, 8, 8, 0, 0, 0, true);
  const outputContainer = document.getElementById('output-container');
  if (outputContainer) {
    outputContainer.style.display = 'none';
    outputContainer.classList.remove('warning', 'success');
  }
}

document.getElementById('preview-btn').addEventListener('click', runPreview);

const download = async (button, type) => {
  button.classList.add('button-disabled');
  updateOutput('Processing...', false);
  const code = document.getElementById('code-input').value;
  try {
    const response = await fetch(api + type, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    });
    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.message || 'Failed to generate ' + type.toUpperCase());
    }
    let filename = 'model.' + type;
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
    updateOutput(type.toUpperCase() + ' file generated successfully', true);
  } catch (error) {
    console.error(error);
    updateOutput('Error: ' + error.message, false);
  }
  button.classList.remove('button-disabled');
};

const stl_button = document.getElementById('stl-btn');
const step_button = document.getElementById('step-btn');
stl_button.addEventListener('click', () => download(stl_button, 'stl'));
step_button.addEventListener('click', () => download(step_button, 'step'));

const clock = new THREE.Clock();

function animate() {
  const delta = clock.getDelta();
  cameraControls.update(delta);
  requestAnimationFrame(animate);
  renderer.render(scene, camera);
}
animate();

function resizeViewer() {
  const width = viewerContainer.clientWidth;
  const height = viewerContainer.clientHeight;
  if (width === 0 || height === 0) return;
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
  renderer.setSize(width, height);
}

window.addEventListener('resize', resizeViewer);
const ro = new ResizeObserver(resizeViewer);
ro.observe(viewerContainer);

// --- Resizable panels ---
//
// resizer-1 splits agent sidebar ↔ editor: their pixel widths swap (sum preserved).
// resizer-2 splits editor ↔ viewer: same logic.
// On window resize we leave whatever the user picked alone (>=1200px viewport).
function setupResizers() {
  const agentSidebar = document.getElementById('agent-sidebar');
  const editorPanel = document.getElementById('editor-panel');
  const viewerPanel = document.getElementById('viewer-panel');
  const r1 = document.getElementById('resizer-1');
  const r2 = document.getElementById('resizer-2');
  const MIN = 220;

  let dragging = null;
  let startX = 0;
  let startSidebar = 0, startEditor = 0, startViewer = 0;

  const onMove = (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    if (dragging === 'r1') {
      const total = startSidebar + startEditor;
      const newSidebar = Math.max(MIN, Math.min(total - MIN, startSidebar + dx));
      const newEditor = total - newSidebar;
      agentSidebar.style.flex = `0 0 ${newSidebar}px`;
      editorPanel.style.flex = `0 0 ${newEditor}px`;
    } else if (dragging === 'r2') {
      const total = startEditor + startViewer;
      const newEditor = Math.max(MIN, Math.min(total - MIN, startEditor + dx));
      const newViewer = total - newEditor;
      editorPanel.style.flex = `0 0 ${newEditor}px`;
      viewerPanel.style.flex = `0 0 ${newViewer}px`;
    }
    resizeViewer();
  };

  const onUp = () => {
    if (!dragging) return;
    dragging = null;
    document.body.style.cursor = '';
    [r1, r2].forEach(r => r.classList.remove('resizing'));
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    resizeViewer();
  };

  const start = (which, resizerEl) => (e) => {
    if (e.button !== 0) return;
    dragging = which;
    startX = e.clientX;
    startSidebar = agentSidebar.getBoundingClientRect().width;
    startEditor = editorPanel.getBoundingClientRect().width;
    startViewer = viewerPanel.getBoundingClientRect().width;
    resizerEl.classList.add('resizing');
    document.body.style.cursor = 'col-resize';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    e.preventDefault();
  };

  r1.addEventListener('mousedown', start('r1', r1));
  r2.addEventListener('mousedown', start('r2', r2));
}

setupResizers();
