import top from '../../assets/robots/top.png'
import rear from '../../assets/robots/rear.png'
import left from '../../assets/robots/left.png'
import front from '../../assets/robots/front.png'
import right from '../../assets/robots/right.png'
import isometric from '../../assets/robots/isometric.png'

// Generic model illustrations, not photographs identifying the selected VIN.
export const ROBOT_PHOTOS = [
  { id: 'top', title: 'Вид сверху', src: top, width: 2269, height: 2347 },
  { id: 'rear', title: 'Вид сзади', src: rear, width: 1454, height: 2204 },
  { id: 'left', title: 'Вид слева', src: left, width: 1610, height: 2263 },
  { id: 'front', title: 'Вид спереди', src: front, width: 1547, height: 2176 },
  { id: 'right', title: 'Вид справа', src: right, width: 1638, height: 2325 },
  { id: 'isometric', title: 'Изометрия', src: isometric, width: 1962, height: 2225 },
] as const

export type RobotPhotoId = typeof ROBOT_PHOTOS[number]['id']
