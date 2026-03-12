# ha-etalon-uksn
Интеграция для взаимодействия с ЛК УК Сервис-Недвижимость (Эталон / Платформа OICO)

```
type: custom:apexcharts-card
graph_span: 5y

apex_config:
  chart:
    type: bar

series:
  - entity: sensor.street_upravl_raskhody_itogo
    name: Отопление

    transform: |
      return null;

    data_generator: |
      const data = (entity.attributes.history_preview || [])
      .sort((a,b)=>a.month_key.localeCompare(b.month_key));

      return data.map((item) => {
        return [
          new Date(item.month_key + "-01").getTime(),
          item.value
        ];
      });
```
