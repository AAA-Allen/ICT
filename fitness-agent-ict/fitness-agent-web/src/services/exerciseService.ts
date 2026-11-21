import { queryCatalog } from './catalogSource'
import type { Exercise } from '../types/exercise'
import type { TrainingConfiguration, TrainingLevel } from '../types/training'

const levelOrder: Record<TrainingLevel, number> = { beginner: 0, intermediate: 1, advanced: 2 }

// 同步筛选当前资料快照；快照可来自本地或已读取的 Java 资料，不是 Agent 推荐。
export function getLocalExercises(configuration: TrainingConfiguration): Exercise[] {
  return queryCatalog.value.exercises.filter(
    (exercise) =>
      exercise.targetMuscles.includes(configuration.muscleId) &&
      exercise.goals.includes(configuration.goal) &&
      levelOrder[exercise.difficulty] <= levelOrder[configuration.level] &&
      // 有哑铃或弹力带时，也允许显示不使用专用器械的动作。
      (exercise.equipment === 'none' || exercise.equipment === configuration.equipment),
  )
}
