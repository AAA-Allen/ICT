import * as THREE from 'three'
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js'

export type Station = [x: number, y: number, z: number, width: number, depth: number]
export type PatchPoint = [angle: number, height: number]

// 平滑截面沿中心线形成连续的身体曲面，肌群分区沿同一曲面铺设。
export function createBodySurface(stations: Station[]) {
  const center = new THREE.CatmullRomCurve3(stations.map(([x, y, z]) => new THREE.Vector3(x, y, z)))
  const radii = new THREE.CatmullRomCurve3(
    stations.map(([, , , width, depth]) => new THREE.Vector3(width, depth, 0)),
  )
  const heights = Array.from({ length: 257 }, (_, i) => center.getPoint(i / 256).y)
  function point(t: number, angle: number, offset = 0) {
    const c = center.getPoint(t)
    const r = radii.getPoint(t)
    const tangent = center.getTangent(t)
    const right = new THREE.Vector3(tangent.y, -tangent.x, 0).normalize()
    return c
      .addScaledVector(right, (Math.max(0.002, r.x) + offset) * Math.sin(angle))
      .add(new THREE.Vector3(0, 0, (Math.max(0.002, r.y) + offset) * Math.cos(angle)))
  }
  function atHeight(y: number, angle: number, offset = 0) {
    let low = 0
    let high = 256
    while (high - low > 1) {
      const middle = Math.floor((low + high) / 2)
      if (heights[middle]! < y) low = middle
      else high = middle
    }
    const fraction = THREE.MathUtils.clamp(
      (y - heights[low]!) / (heights[high]! - heights[low]!),
      0,
      1,
    )
    return point((low + fraction) / 256, angle, offset)
  }
  const positions: number[] = []
  const indices: number[] = []
  const rings = 72
  const sides = 48
  for (let row = 0; row <= rings; row++) {
    for (let col = 0; col <= sides; col++)
      positions.push(...point(row / rings, (col / sides) * Math.PI * 2).toArray())
  }
  for (let row = 0; row < rings; row++) {
    for (let col = 0; col < sides; col++) {
      const a = row * (sides + 1) + col
      const b = a + sides + 1
      indices.push(a, a + 1, b, a + 1, b + 1, b)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setIndex(indices)
  geometry.computeVertexNormals()

  function patch(points: PatchPoint[]) {
    const curve = new THREE.CatmullRomCurve3(
      points.map(([angle, y]) => new THREE.Vector3(angle, y, 0)),
      true,
      'centripetal',
    )
    const contour = curve
      .getPoints(64)
      .slice(0, -1)
      .map((p) => new THREE.Vector2(p.x, p.y))
    const faces = THREE.ShapeUtils.triangulateShape(contour, [])
    const vertices: number[] = []
    function triangle(a: THREE.Vector2, b: THREE.Vector2, c: THREE.Vector2, depth: number) {
      if (depth) {
        const ab = a.clone().add(b).multiplyScalar(0.5)
        const bc = b.clone().add(c).multiplyScalar(0.5)
        const ca = c.clone().add(a).multiplyScalar(0.5)
        triangle(a, ab, ca, depth - 1)
        triangle(ab, b, bc, depth - 1)
        triangle(ca, bc, c, depth - 1)
        triangle(ab, bc, ca, depth - 1)
      } else {
        for (const v of [a, b, c]) vertices.push(...atHeight(v.y, v.x, 0.025).toArray())
      }
    }
    for (const [a, b, c] of faces) triangle(contour[a!]!, contour[b!]!, contour[c!]!, 3)
    const source = new THREE.BufferGeometry()
    source.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
    const skin = mergeVertices(source)
    source.dispose()
    skin.computeVertexNormals()
    const border = new THREE.BufferGeometry().setFromPoints(
      contour.map((p) => atHeight(p.y, p.x, 0.031)),
    )
    return { geometry: skin, border }
  }
  return { geometry, patch }
}
