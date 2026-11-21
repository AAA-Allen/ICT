import * as THREE from 'three'
import { createBodySurface } from './bodyGeometry'
import type { Station, PatchPoint } from './bodyGeometry'
import type { MuscleId } from '../types/muscle'
import type { BodyModelStyle } from '../types/bodyModel'

type Triple = [number, number, number]
type BodyMesh = THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>

// 颜色参考用户提供的分区示意；几何、曲面分区及表情均由项目原创生成。
const palette: Record<MuscleId, string> = {
  chest: '#9dd8e4',
  deltoids: '#b8dfb1',
  trapezius: '#e5aaaa',
  lats: '#b9abdf',
  biceps: '#b2bce5',
  triceps: '#b3dfe0',
  abs: '#e9a09a',
  glutes: '#c7addf',
  quads: '#a6cce8',
  hamstrings: '#d9b3d5',
  calves: '#b3d9ca',
}

export function createBodyModel(style: BodyModelStyle = 'human') {
  const group = new THREE.Group()
  const surfaces: BodyMesh[] = []
  const selectable = new Map<MuscleId, BodyMesh[]>()
  const cute = style === 'cute'
  const skin = new THREE.MeshStandardMaterial({
    color: cute ? '#f5dfcd' : '#eee6cf',
    roughness: 0.78,
  })
  const sphere = new THREE.SphereGeometry(1, 32, 24)
  const borderMaterial = new THREE.LineBasicMaterial({
    color: cute ? '#927b72' : '#687c7d',
    transparent: true,
    opacity: cute ? 0.3 : 0.42,
  })

  function add(
    geometry: THREE.BufferGeometry,
    material: THREE.MeshStandardMaterial,
    side = 1,
    id?: MuscleId,
  ) {
    const mesh = new THREE.Mesh(geometry, material)
    mesh.scale.x = side
    mesh.userData.muscleId = id
    group.add(mesh)
    surfaces.push(mesh)
    if (id) {
      mesh.name = id
      const parts = selectable.get(id) ?? []
      parts.push(mesh)
      selectable.set(id, parts)
    }
    return mesh
  }
  function ellipsoid(position: Triple, scale: Triple, color?: string, tilt = 0) {
    const material = color ? new THREE.MeshStandardMaterial({ color, roughness: 0.75 }) : skin
    const mesh = add(sphere, material)
    mesh.position.set(...position)
    mesh.scale.set(...scale)
    mesh.rotation.z = tilt
    return mesh
  }
  function body(stations: Station[], side = 1) {
    const surface = createBodySurface(stations)
    add(surface.geometry, skin, side)
    return {
      region(id: MuscleId, points: PatchPoint[]) {
        const { geometry, border } = surface.patch(points)
        const color = new THREE.Color(palette[id])
        if (cute) color.lerp(new THREE.Color('#ffe8d9'), 0.22)
        const material = new THREE.MeshStandardMaterial({
          color,
          roughness: 0.8,
          side: THREE.DoubleSide,
          polygonOffset: true,
          polygonOffsetFactor: -1,
          polygonOffsetUnits: -1,
        })
        material.userData.baseColor = color.clone()
        add(geometry, material, side, id)
        const line = new THREE.LineLoop(border, borderMaterial)
        line.scale.x = side
        group.add(line)
      },
    }
  }
  function stroke(points: Triple[], color: string, radius: number) {
    const geometry = new THREE.TubeGeometry(
      new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p))),
      24,
      radius,
      6,
      false,
    )
    add(geometry, new THREE.MeshStandardMaterial({ color, roughness: 0.8 }))
  }

  if (!cute) {
    const torso = body([
      [0, 3.36, 0, 0.08, 0.1],
      [0, 3.6, 0, 0.51, 0.32],
      [0, 3.94, 0, 0.65, 0.37],
      [0, 4.28, 0, 0.55, 0.3],
      [0, 4.6, 0, 0.49, 0.27],
      [0, 4.94, 0, 0.56, 0.32],
      [0, 5.28, 0, 0.71, 0.37],
      [0, 5.64, 0, 0.82, 0.39],
      [0, 5.96, 0, 0.83, 0.34],
      [0, 6.12, 0, 0.73, 0.28],
      [0, 6.29, 0, 0.35, 0.22],
      [0, 6.44, 0, 0.2, 0.19],
      [0, 6.7, 0, 0.19, 0.18],
      [0, 6.76, 0, 0.08, 0.08],
    ])
    body([
      [0, 6.66, 0.03, 0.09, 0.1],
      [0, 6.79, 0.04, 0.23, 0.22],
      [0, 6.96, 0.01, 0.29, 0.3],
      [0, 7.19, 0, 0.35, 0.33],
      [0, 7.42, -0.01, 0.34, 0.31],
      [0, 7.59, -0.02, 0.2, 0.2],
      [0, 7.65, -0.02, 0.002, 0.002],
    ])
    ellipsoid([0, 7.09, 0.327], [0.043, 0.105, 0.047])
    for (const side of [-1, 1]) {
      ellipsoid([side * 0.345, 7.13, 0], [0.05, 0.105, 0.06])
      stroke(
        [
          [side * 0.09, 7.2, 0.322],
          [side * 0.16, 7.21, 0.301],
          [side * 0.22, 7.2, 0.275],
        ],
        '#8b8b7c',
        0.009,
      )
      const mirror = (points: PatchPoint[]) => points.map(([a, y]): PatchPoint => [side * a, y])
      torso.region(
        'chest',
        mirror([
          [0.06, 5.97],
          [0.65, 6.02],
          [1.18, 5.83],
          [1.11, 5.5],
          [0.48, 5.36],
          [0.05, 5.49],
        ]),
      )
      torso.region(
        'trapezius',
        mirror([
          [3.07, 6.57],
          [2.21, 6.13],
          [1.91, 5.99],
          [2.64, 5.77],
          [3.06, 5.36],
        ]),
      )
      torso.region(
        'lats',
        mirror([
          [2.51, 5.77],
          [1.28, 5.71],
          [1.08, 5.33],
          [1.66, 4.72],
          [2.94, 4.52],
          [2.84, 5.2],
        ]),
      )
      torso.region(
        'glutes',
        mirror([
          [3.08, 4.08],
          [2.2, 4.1],
          [1.72, 3.85],
          [2.0, 3.58],
          [2.75, 3.49],
          [3.09, 3.64],
        ]),
      )
      for (const [low, high] of [
        [4.25, 4.56],
        [4.6, 4.93],
        [4.97, 5.3],
      ]) {
        torso.region(
          'abs',
          mirror([
            [0.055, high!],
            [0.47, high! + 0.015],
            [0.52, low! + 0.07],
            [0.35, low!],
            [0.055, low! + 0.025],
          ]),
        )
      }
      torso.region(
        'abs',
        mirror([
          [0.7, 5.2],
          [1.2, 5.11],
          [1.02, 4.52],
          [0.58, 4.24],
          [0.63, 4.7],
        ]),
      )
      const arm = body(
        [
          [1.63, 3.9, 0.02, 0.08, 0.08],
          [1.59, 4.08, 0.01, 0.12, 0.12],
          [1.47, 4.4, 0, 0.18, 0.16],
          [1.33, 4.81, 0, 0.17, 0.16],
          [1.22, 5.12, 0, 0.22, 0.2],
          [1.08, 5.5, 0, 0.245, 0.23],
          [0.94, 5.88, 0, 0.285, 0.27],
          [0.87, 6.08, 0, 0.245, 0.23],
          [0.83, 6.19, 0, 0.06, 0.06],
        ],
        side,
      )
      arm.region('deltoids', [
        [-0.63, 5.99],
        [0.29, 6.13],
        [2.16, 6.1],
        [3.83, 5.94],
        [3.42, 5.67],
        [1.5, 5.59],
        [0.04, 5.69],
      ])
      arm.region('biceps', [
        [-0.8, 5.58],
        [0.57, 5.62],
        [1.18, 5.33],
        [0.56, 5.01],
        [-0.61, 5.06],
        [-1.0, 5.3],
      ])
      arm.region('triceps', [
        [2.23, 5.6],
        [3.43, 5.65],
        [4.12, 5.35],
        [3.7, 5.03],
        [2.34, 5.08],
        [2.14, 5.34],
      ])
      const leg = body(
        [
          [0.44, 0.3, 0, 0.11, 0.12],
          [0.44, 0.55, 0, 0.13, 0.14],
          [0.45, 0.91, -0.02, 0.18, 0.2],
          [0.45, 1.38, -0.02, 0.23, 0.25],
          [0.44, 1.83, 0, 0.2, 0.2],
          [0.43, 2.11, 0.02, 0.19, 0.2],
          [0.4, 2.55, 0, 0.27, 0.27],
          [0.36, 3.04, 0, 0.32, 0.31],
          [0.33, 3.52, 0, 0.34, 0.32],
          [0.32, 3.79, 0, 0.24, 0.24],
          [0.31, 3.9, 0, 0.1, 0.1],
        ],
        side,
      )
      leg.region('quads', [
        [-1.05, 3.46],
        [0.38, 3.61],
        [1.22, 3.4],
        [1.26, 2.84],
        [0.58, 2.22],
        [-0.65, 2.22],
        [-1.12, 2.84],
      ])
      leg.region('hamstrings', [
        [2.15, 3.48],
        [3.22, 3.55],
        [4.18, 3.38],
        [4.1, 2.63],
        [3.53, 2.22],
        [2.38, 2.31],
        [2.05, 2.88],
      ])
      leg.region('calves', [
        [2.14, 1.88],
        [3.14, 1.98],
        [4.16, 1.85],
        [4.08, 1.3],
        [3.27, 0.65],
        [2.38, 1.15],
      ])
      ellipsoid([side * 0.44, 0.17, 0.18], [0.18, 0.14, 0.38])
      ellipsoid([side * 1.68, 3.76, 0.025], [0.14, 0.23, 0.095], undefined, side * 0.2)
      for (let finger = 0; finger < 4; finger++)
        ellipsoid(
          [side * (1.61 + finger * 0.046), 3.55 - Math.sin((finger / 3) * Math.PI) * 0.035, 0.034],
          [0.033, 0.15, 0.049],
          undefined,
          side * 0.13,
        )
      ellipsoid([side * 1.52, 3.76, 0.09], [0.055, 0.13, 0.06], undefined, -side * 0.5)
    }
  } else {
    const torso = body([
      [0, 2.32, 0, 0.17, 0.2],
      [0, 2.52, 0, 0.6, 0.4],
      [0, 2.86, 0, 0.69, 0.45],
      [0, 3.3, 0, 0.65, 0.46],
      [0, 3.73, 0, 0.64, 0.43],
      [0, 4.08, 0, 0.68, 0.4],
      [0, 4.37, 0, 0.63, 0.35],
      [0, 4.57, 0, 0.36, 0.25],
      [0, 4.78, 0, 0.2, 0.21],
    ])
    // 三头身比例，平缓的下巴和额头形成大头轮廓。
    body([
      [0, 4.72, 0.02, 0.16, 0.19],
      [0, 4.91, 0.03, 0.57, 0.52],
      [0, 5.19, 0, 1.02, 0.73],
      [0, 5.62, -0.03, 1.29, 0.87],
      [0, 6.13, -0.06, 1.35, 0.93],
      [0, 6.67, -0.07, 1.3, 0.9],
      [0, 7.17, -0.08, 1.1, 0.77],
      [0, 7.51, -0.09, 0.65, 0.48],
      [0, 7.65, -0.09, 0.002, 0.002],
    ])
    for (const side of [-1, 1]) {
      ellipsoid([side * 1.29, 5.65, -0.02], [0.23, 0.32, 0.18])
      ellipsoid([side * 1.34, 5.65, 0.105], [0.1, 0.18, 0.055], '#e8b6a8')
      // 大眼、双高光、眉毛和腮红。
      ellipsoid([side * 0.5, 5.79, 0.813], [0.29, 0.4, 0.105], '#51464b')
      ellipsoid([side * 0.5, 5.75, 0.899], [0.2, 0.29, 0.035], '#8d7d83')
      ellipsoid([side * 0.45, 5.94, 0.923], [0.095, 0.125, 0.021], '#fffaf4')
      ellipsoid([side * 0.58, 5.61, 0.926], [0.044, 0.059, 0.018], '#fffaf4')
      ellipsoid([side * 0.87, 5.4, 0.685], [0.16, 0.085, 0.035], '#efb3ac')
      stroke(
        [
          [side * 0.28, 6.4, 0.826],
          [side * 0.46, 6.46, 0.805],
          [side * 0.68, 6.42, 0.741],
        ],
        '#9c7b71',
        0.024,
      )
      const mirror = (points: PatchPoint[]) => points.map(([a, y]): PatchPoint => [side * a, y])
      torso.region(
        'chest',
        mirror([
          [0.07, 4.32],
          [0.65, 4.35],
          [1.17, 4.16],
          [0.98, 3.93],
          [0.4, 3.88],
          [0.07, 3.98],
        ]),
      )
      torso.region(
        'abs',
        mirror([
          [0.07, 3.8],
          [0.69, 3.77],
          [0.73, 3.31],
          [0.51, 2.94],
          [0.07, 2.9],
        ]),
      )
      torso.region(
        'trapezius',
        mirror([
          [3.07, 4.69],
          [2.12, 4.41],
          [1.96, 4.18],
          [2.7, 4.0],
          [3.07, 3.91],
        ]),
      )
      torso.region(
        'lats',
        mirror([
          [2.54, 3.99],
          [1.46, 4.03],
          [1.32, 3.6],
          [2.1, 3.15],
          [3.0, 3.15],
          [2.85, 3.62],
        ]),
      )
      torso.region(
        'glutes',
        mirror([
          [3.07, 3.04],
          [2.18, 3.05],
          [1.73, 2.74],
          [2.17, 2.48],
          [2.95, 2.45],
          [3.08, 2.66],
        ]),
      )
      const arm = body(
        [
          [1.87, 2.96, 0.02, 0.13, 0.14],
          [1.79, 3.12, 0.01, 0.2, 0.19],
          [1.55, 3.4, 0, 0.22, 0.21],
          [1.28, 3.72, 0, 0.23, 0.22],
          [1.02, 4.03, 0, 0.26, 0.25],
          [0.77, 4.33, 0, 0.27, 0.26],
          [0.67, 4.47, 0, 0.08, 0.09],
        ],
        side,
      )
      arm.region('deltoids', [
        [-0.55, 4.27],
        [0.45, 4.43],
        [2.5, 4.38],
        [3.8, 4.24],
        [3.33, 4.02],
        [1.37, 3.99],
        [0.06, 4.04],
      ])
      arm.region('biceps', [
        [-0.84, 4.0],
        [0.65, 4.04],
        [1.11, 3.82],
        [0.56, 3.53],
        [-0.62, 3.57],
        [-0.95, 3.79],
      ])
      arm.region('triceps', [
        [2.24, 3.98],
        [3.47, 4.04],
        [4.08, 3.83],
        [3.6, 3.53],
        [2.36, 3.58],
      ])
      ellipsoid([side * 1.91, 2.88, 0.04], [0.23, 0.22, 0.19], undefined, -side * 0.6)
      ellipsoid([side * 1.73, 2.98, 0.17], [0.1, 0.13, 0.1])
      const leg = body(
        [
          [0.47, 0.22, 0.03, 0.17, 0.19],
          [0.46, 0.54, 0, 0.24, 0.25],
          [0.43, 1.0, 0, 0.26, 0.29],
          [0.4, 1.36, 0.01, 0.25, 0.26],
          [0.37, 1.76, 0, 0.29, 0.3],
          [0.34, 2.18, 0, 0.33, 0.33],
          [0.32, 2.56, 0, 0.34, 0.33],
          [0.3, 2.75, 0, 0.18, 0.19],
        ],
        side,
      )
      leg.region('quads', [
        [-1.0, 2.35],
        [0.48, 2.51],
        [1.24, 2.28],
        [1.13, 1.83],
        [0.55, 1.51],
        [-0.67, 1.51],
        [-1.12, 1.9],
      ])
      leg.region('hamstrings', [
        [2.13, 2.34],
        [3.25, 2.48],
        [4.14, 2.29],
        [4.03, 1.81],
        [3.53, 1.49],
        [2.37, 1.57],
      ])
      leg.region('calves', [
        [2.15, 1.26],
        [3.16, 1.35],
        [4.13, 1.23],
        [3.99, 0.83],
        [3.23, 0.43],
        [2.38, 0.77],
      ])
      ellipsoid([side * 0.48, 0.2, 0.2], [0.29, 0.2, 0.43])
    }
    ellipsoid([0, 5.65, 0.89], [0.062, 0.044, 0.04], '#ecc5b2')
  }
  group.updateMatrixWorld(true)

  function highlight(selected: MuscleId | null, hovered: MuscleId | null) {
    selectable.forEach((parts, id) =>
      parts.forEach(({ material }) => {
        const base = material.userData.baseColor as THREE.Color
        material.color.copy(base)
        if (id === selected) material.color.lerp(new THREE.Color('#235d55'), 0.72)
        else if (id === hovered) material.color.lerp(new THREE.Color('#fff4c2'), 0.4)
        material.emissive.set(id === selected ? '#183a32' : '#000000')
        material.emissiveIntensity = id === selected ? 0.12 : 0
      }),
    )
  }
  function dispose() {
    const geometries = new Set<THREE.BufferGeometry>()
    const materials = new Set<THREE.Material>()
    group.traverse((object) => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
        geometries.add(object.geometry)
        const entries = Array.isArray(object.material) ? object.material : [object.material]
        entries.forEach((material) => materials.add(material))
      }
    })
    geometries.forEach((geometry) => geometry.dispose())
    materials.forEach((material) => material.dispose())
  }
  return { group, surfaces, highlight, dispose }
}
