import { expect, it } from 'vitest'
import { summarizeIssueDescription } from './issueDescription'

it('keeps only the useful template fields from robot 1217 including repair notes', () => {
  const source = `**SUF**: робот находится под управлением системы SUF
**Classificator:** Disk space
**Comment:** Недостаточно места на диске
**Zone:** Lavka Smolensky
**Port:** Moscow Robot
**Partner:** lavka
**Time:** 2026-09-03 15:44:41
**Rover name:** a1217
**Mode:** AUTO_MODE_AUTO
**Reported mode:** AUTO
Что было сделано: Агрессивная чистка, почищены старые докер-образы. Рекомендации: Забит логами, необходимо слить по шнурку.`
  expect(summarizeIssueDescription(source)).toBe(`**Classificator:** Disk space

**Comment:** Недостаточно места на диске

**Zone:** Lavka Smolensky

**Что было сделано:** Агрессивная чистка, почищены старые докер-образы.

**Рекомендации:** Забит логами, необходимо слить по шнурку.`)
})

it('handles inline fields, both bold colon styles, whitespace entities and multiline recommendations', () => {
  expect(summarizeIssueDescription('**Classificator**: Disk space **Comment:** Недостаточно места **Zone:** Lavka Smolensky **Port:** Moscow\n\n**Рекомендации:**\n- Подключить кабель\n- Слить логи\n**Internal data:** hidden'))
    .toBe('**Classificator:** Disk space\n\n**Comment:** Недостаточно места\n\n**Zone:** Lavka Smolensky\n\n**Рекомендации:**\n\n- Подключить кабель\n- Слить логи')
  expect(summarizeIssueDescription('Classificator:\u00a0Disk space Comment: Ошибка Zone: Lavka Port: Moscow')).toBe('**Classificator:** Disk space\n\n**Comment:** Ошибка\n\n**Zone:** Lavka')
})

it('keeps ordinary manually written descriptions and omits empty template fields', () => {
  const manual = '**Важно**\nПроверить крепление перед выездом.\nПричина: после ремонта.\n\n[Инструкция](https://example.org)'
  expect(summarizeIssueDescription(manual)).toBe(manual)
  expect(summarizeIssueDescription('Classificator: Disk space\nComment:\nZone: Lavka\nPort: Moscow')).toBe('**Classificator:** Disk space\n\n**Zone:** Lavka')
})

it('removes Tracker control wrappers without dropping their useful text', () => {
  expect(summarizeIssueDescription('<[robotBlock]>\n<{service data}>\nПроверить крепление\n<[end]>'))
    .toBe('Проверить крепление')
  expect(summarizeIssueDescription('Заменить <[колесо]> и проверить <{подвеску}>'))
    .toBe('Заменить колесо и проверить подвеску')
})
