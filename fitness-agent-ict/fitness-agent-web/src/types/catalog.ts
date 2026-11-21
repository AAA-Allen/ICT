import type { MuscleGroup } from './muscle'
import type { Exercise } from './exercise'
import type { DemonstrationRecord } from './demonstration'

export interface QueryCatalog {
  muscles: MuscleGroup[]
  exercises: Exercise[]
  demonstrations: DemonstrationRecord[]
}

export type CatalogSource = 'local' | 'java'
