import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { createBodyModel } from './bodyModel'
import { preferredBodyView } from '../data/muscleMap'
import type { MuscleId } from '../types/muscle'
import type { BodyView } from '../types/muscleMap'
import type { BodyModelStyle } from '../types/bodyModel'

export interface CameraState {
  view: BodyView | 'free'
  angle: number
  distance: number
  canZoomIn: boolean
  canZoomOut: boolean
}

interface SceneEvents {
  select: (id: MuscleId) => void
  hover: (id: MuscleId | null) => void
  camera: (state: CameraState) => void
  error: () => void
}

export function createMuscleScene(
  canvas: HTMLCanvasElement,
  events: SceneEvents,
  initialStyle: BodyModelStyle = 'human',
) {
  const context = canvas.getContext('webgl2', { alpha: true, antialias: true })
  if (!context) throw new Error('WebGL 2 unavailable')
  const renderer = new THREE.WebGLRenderer({ canvas, context, antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75))
  renderer.setClearColor(0x000000, 0)
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.15
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 80)
  const target = new THREE.Vector3(0, 3.82, 0)
  camera.position.set(3.5, 3.82, 14)
  let modelStyle = initialStyle
  let model = createBodyModel(modelStyle)
  scene.add(model.group)
  scene.add(new THREE.HemisphereLight('#ffffee', '#667565', 2.5))
  const key = new THREE.DirectionalLight('#fff4d9', 3.5)
  key.position.set(-4, 9, 7)
  const fill = new THREE.DirectionalLight('#e5efe6', 2)
  fill.position.set(5, 6, -6)
  scene.add(key, fill)

  const platform = new THREE.Mesh(
    new THREE.CylinderGeometry(1.9, 1.9, 0.06, 64),
    new THREE.MeshStandardMaterial({ color: '#dce4d3', roughness: 0.9 }),
  )
  platform.position.y = -0.02
  scene.add(platform)
  const ring = new THREE.Mesh(
    new THREE.TorusGeometry(1.71, 0.012, 6, 80),
    new THREE.MeshBasicMaterial({ color: '#9faf92' }),
  )
  ring.rotation.x = Math.PI / 2
  ring.position.y = 0.017
  scene.add(ring)

  const controls = new OrbitControls(camera, canvas)
  controls.target.copy(target)
  controls.enablePan = false
  controls.enableDamping = false // 按需渲染，静止时不运行持续动画循环。
  controls.minPolarAngle = Math.PI / 3
  controls.maxPolarAngle = (Math.PI * 2) / 3
  controls.rotateSpeed = 0.65
  controls.zoomSpeed = 0.7
  controls.update()
  const raycaster = new THREE.Raycaster()
  let selected: MuscleId | null = null
  let hovered: MuscleId | null = null
  let disposed = false
  let failed = false
  let frame = 0
  let fittedDistance = 15
  let initialized = false

  function render() {
    if (disposed || failed || frame) return
    frame = requestAnimationFrame(() => {
      frame = 0
      if (disposed || failed) return
      try {
        renderer.render(scene, camera)
      } catch {
        failed = true
        events.error()
      }
    })
  }

  function reportCamera() {
    const angle = Math.atan2(camera.position.x, camera.position.z)
    const polar = controls.getPolarAngle()
    const level = Math.abs(polar - Math.PI / 2) < 0.03
    const distance = camera.position.distanceTo(target)
    events.camera({
      view:
        level && Math.abs(angle) < 0.03
          ? 'front'
          : level && Math.abs(Math.abs(angle) - Math.PI) < 0.03
            ? 'back'
            : 'free',
      angle,
      distance,
      canZoomIn: distance > controls.minDistance + 0.01,
      canZoomOut: distance < controls.maxDistance - 0.01,
    })
    render()
  }

  function pose(angle: number, distance = camera.position.distanceTo(target)) {
    camera.position.set(Math.sin(angle) * distance, target.y, Math.cos(angle) * distance)
    controls.update()
    reportCamera()
  }

  function resize() {
    const { width, height } = canvas.getBoundingClientRect()
    if (!width || !height) return
    const previousFit = fittedDistance
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    fittedDistance = Math.max(4.25, 2.18 / camera.aspect) / Math.tan(THREE.MathUtils.degToRad(16))
    controls.minDistance = fittedDistance * 0.55
    controls.maxDistance = fittedDistance * 1.35
    renderer.setSize(width, height, false)
    const ratio = initialized ? camera.position.distanceTo(target) / previousFit : 1
    camera.position
      .sub(target)
      .setLength(fittedDistance * ratio)
      .add(target)
    initialized = true
    controls.update()
    reportCamera()
  }

  function pick(event: PointerEvent): MuscleId | null {
    const rect = canvas.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    const y = -((event.clientY - rect.top) / rect.height) * 2 + 1
    if (Math.abs(x) > 1 || Math.abs(y) > 1) return null
    camera.updateMatrixWorld()
    raycaster.setFromCamera(new THREE.Vector2(x, y), camera)
    // 检查完整实体的最近表面，不能穿过胸部选到背部肌肉。
    const hit = raycaster.intersectObjects(model.surfaces, false)[0]
    return (hit?.object.userData.muscleId as MuscleId | undefined) ?? null
  }

  function hover(id: MuscleId | null) {
    if (hovered === id) return
    hovered = id
    canvas.style.cursor = id ? 'pointer' : 'grab'
    events.hover(id)
    model.highlight(selected, hovered)
    render()
  }

  const pointers = new Set<number>()
  let tap: { id: number; x: number; y: number } | null = null
  function pointerDown(event: PointerEvent) {
    pointers.add(event.pointerId)
    tap =
      pointers.size === 1 && event.button === 0
        ? { id: event.pointerId, x: event.clientX, y: event.clientY }
        : null
    hover(null)
  }
  function pointerMove(event: PointerEvent) {
    if (tap && Math.hypot(event.clientX - tap.x, event.clientY - tap.y) > 6) tap = null
    if (!pointers.size && event.pointerType !== 'touch') hover(pick(event))
  }
  function pointerUp(event: PointerEvent) {
    const isTap =
      tap?.id === event.pointerId &&
      pointers.size === 1 &&
      Math.hypot(event.clientX - tap.x, event.clientY - tap.y) <= 6
    pointers.delete(event.pointerId)
    tap = null
    if (isTap) {
      const id = pick(event)
      if (id) events.select(id)
    }
  }
  function pointerCancel(event: PointerEvent) {
    pointers.delete(event.pointerId)
    tap = null
    hover(null)
  }
  function pointerLeave() {
    hover(null)
  }
  function contextLost(event: Event) {
    event.preventDefault()
    failed = true
    events.error()
  }
  function keyDown(event: KeyboardEvent) {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      pose(controls.getAzimuthalAngle() + (event.key === 'ArrowLeft' ? -0.2 : 0.2))
    } else if (event.key === '+' || event.key === '=' || event.key === '-') {
      event.preventDefault()
      zoom(event.key === '-' ? 1.15 : 1 / 1.15)
    } else if (event.key === 'Home') {
      event.preventDefault()
      reset()
    }
  }

  function zoom(factor: number) {
    const distance = THREE.MathUtils.clamp(
      camera.position.distanceTo(target) * factor,
      controls.minDistance,
      controls.maxDistance,
    )
    camera.position.sub(target).setLength(distance).add(target)
    controls.update()
    reportCamera()
  }
  function reset() {
    pose(0.24, fittedDistance)
  }

  controls.addEventListener('change', reportCamera)
  canvas.addEventListener('pointerdown', pointerDown)
  canvas.addEventListener('pointermove', pointerMove)
  canvas.addEventListener('pointerup', pointerUp)
  canvas.addEventListener('pointercancel', pointerCancel)
  canvas.addEventListener('lostpointercapture', pointerCancel)
  canvas.addEventListener('pointerleave', pointerLeave)
  canvas.addEventListener('keydown', keyDown)
  canvas.addEventListener('webglcontextlost', contextLost)
  const observer = new ResizeObserver(resize)
  observer.observe(canvas)
  resize()
  reset()

  return {
    setStyle(style: BodyModelStyle) {
      if (modelStyle === style) return
      const replacement = createBodyModel(style)
      scene.remove(model.group)
      model.dispose()
      model = replacement
      modelStyle = style
      scene.add(model.group)
      hover(null)
      model.highlight(selected, null)
      render()
    },
    setSelection(id: MuscleId | null) {
      selected = id
      model.highlight(selected, hovered)
      render()
    },
    focus(id: MuscleId) {
      hover(null)
      // 先恢复完整人体构图，避免放大后定位小腿时目标仍在画面外。
      pose(preferredBodyView(id) === 'front' ? 0 : Math.PI, fittedDistance)
    },
    setView(view: BodyView) {
      hover(null)
      pose(view === 'front' ? 0 : Math.PI)
    },
    zoom,
    reset,
    dispose() {
      disposed = true
      cancelAnimationFrame(frame)
      observer.disconnect()
      controls.removeEventListener('change', reportCamera)
      controls.dispose()
      canvas.removeEventListener('pointerdown', pointerDown)
      canvas.removeEventListener('pointermove', pointerMove)
      canvas.removeEventListener('pointerup', pointerUp)
      canvas.removeEventListener('pointercancel', pointerCancel)
      canvas.removeEventListener('lostpointercapture', pointerCancel)
      canvas.removeEventListener('pointerleave', pointerLeave)
      canvas.removeEventListener('keydown', keyDown)
      canvas.removeEventListener('webglcontextlost', contextLost)
      model.dispose()
      platform.geometry.dispose()
      platform.material.dispose()
      ring.geometry.dispose()
      ring.material.dispose()
      renderer.dispose()
      if (!context.isContextLost()) renderer.forceContextLoss()
    },
  }
}

export type MuscleScene = ReturnType<typeof createMuscleScene>
