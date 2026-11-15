# Развёртывание format-security-pattern в Kubernetes

Каталог `k8s/` содержит Kubernetes-манифесты как альтернативный способ запуска того же стенда.

Основной проверяемый сценарий — локальный Kubernetes в Docker Desktop. Все ресурсы создаются в namespace `format-security`; наружу сервисы открываются только через `kubectl port-forward`.

## Требования

- Docker Desktop с включённым Kubernetes;
- `docker` и `kubectl` в `PATH`;
- стандартный StorageClass кластера для PVC;
- не менее 4 ГБ памяти, доступной Kubernetes.

Проверьте подключение:

```powershell
kubectl cluster-info
kubectl config current-context
```

## 1. Сборка локальных образов

Выполните из корня проекта:

```powershell
docker build -t format-security-gateway:local ./services/gateway
docker build -t format-security-inspector:local ./services/inspector
docker build -t format-security-validator:local ./services/validator
docker build -t format-security-storage:local ./services/storage
docker build -t format-security-audit:local ./services/audit
```

В manifests установлено `imagePullPolicy: IfNotPresent`. Образы должны быть доступны container runtime кластера. Docker Desktop поддерживает разные provisioner (`kubeadm` и `kind`); доступность локальных образов зависит от конфигурации. Проверьте её для выбранного кластера; при необходимости загрузите образы или используйте доступный кластеру registry. См. [документацию Docker Desktop](https://docs.docker.com/desktop/use-desktop/kubernetes/).

## 2. Конфигурация Grafana без дублирования dashboard

Kubernetes ConfigMap создаются непосредственно из существующих файлов `infra/grafana`. Поэтому datasource и dashboard остаются едиными для Docker Compose и Kubernetes.

Сначала создайте namespace:

```powershell
kubectl apply -f k8s/namespace.yaml
```

Затем создайте ConfigMap provisioning и dashboard:

```powershell
kubectl create configmap grafana-provisioning -n format-security --from-file=prometheus.yml=infra/grafana/provisioning/datasources/prometheus.yml --from-file=dashboard-provider.yml=infra/grafana/provisioning/dashboards/format-security.yml --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap grafana-dashboard -n format-security --from-file=format-security.json=infra/grafana/dashboards/format-security.json --dry-run=client -o yaml | kubectl apply -f -
```

Dashboard «Контроль формата входных данных» и Prometheus datasource будут загружены Grafana автоматически. Ручной импорт через Web UI не нужен.

## 3. Развёртывание

Файл `secrets.example.yaml` содержит только демонстрационные значения. Перед использованием вне локального учебного стенда создайте отдельный Secret и замените все пароли. При изменении PostgreSQL-реквизитов значение `DATABASE_URL` должно соответствовать остальным `POSTGRES_*` ключам.

Для локального стенда примените базовую конфигурацию:

```powershell
kubectl apply -f k8s/secrets.example.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/prometheus-config.yaml
```

После создания двух Grafana ConfigMap из предыдущего раздела примените Deployment, Service и PVC явно. Не используйте `kubectl apply -f k8s/` для настроенного стенда: эта команда также применит демонстрационный Secret и может перезаписать ваши реквизиты.

```powershell
kubectl apply -f k8s/gateway.yaml -f k8s/inspector.yaml -f k8s/validator.yaml -f k8s/storage.yaml -f k8s/audit.yaml -f k8s/postgres.yaml -f k8s/minio.yaml -f k8s/prometheus.yaml -f k8s/grafana.yaml
```

Порядок старта pod не фиксируется. Storage повторяет подключение к MinIO и сам создаёт buckets `trusted` и `quarantine`; Audit повторяет подключение к PostgreSQL и сам создаёт таблицу `audit_events`.

Дождитесь готовности:

```powershell
kubectl wait --for=condition=Available deployment --all -n format-security --timeout=300s
kubectl get pods -n format-security
kubectl get services -n format-security
kubectl get pvc -n format-security
```

Ожидается девять Deployment с pod в состоянии `Running` и `READY 1/1`:

- gateway;
- inspector;
- validator;
- storage;
- audit;
- postgres;
- minio;
- prometheus;
- grafana.

## 4. Доступ к интерфейсам

Запустите каждую нужную команду в отдельном терминале. Локальные порты выбраны
так, чтобы Kubernetes-стенд мог работать одновременно с Docker Compose.

Gateway:

```powershell
kubectl port-forward service/gateway 18080:8080 -n format-security
```

Откройте <http://localhost:18080>.

Grafana:

```powershell
kubectl port-forward service/grafana 13000:3000 -n format-security
```

Откройте <http://localhost:13000>. Учётные данные задаются в Kubernetes Secret.

Prometheus:

```powershell
kubectl port-forward service/prometheus 19090:9090 -n format-security
```

Откройте <http://localhost:19090>.

MinIO Console:

```powershell
kubectl port-forward service/minio 19001:9001 -n format-security
```

Откройте <http://localhost:19001>. Учётные данные задаются в Kubernetes Secret. Ссылки на консоли на странице `/status` используют порты Docker Compose; для Kubernetes открывайте адреса port-forward из этого раздела.

## 5. Проверка связности и инициализации

Проверьте Kubernetes DNS из контейнеров приложений:

```powershell
kubectl exec deployment/storage -n format-security -- python -c "import socket; print(socket.gethostbyname('minio'))"
kubectl exec deployment/audit -n format-security -- python -c "import socket; print(socket.gethostbyname('postgres'))"
```

Если Docker Desktop возвращает ошибку CRI exec вида `HTTP response to HTTPS
client`, это сбой канала `kubectl exec`, а не DNS приложения. Для диагностики
можно запустить одноразовый pod на уже собранном локальном образе, прочитать его
вывод и удалить pod:

```powershell
kubectl run dns-check -n format-security --image=format-security-gateway:local --image-pull-policy=IfNotPresent --restart=Never --command -- python -c "import socket; names=['minio','postgres','inspector','validator','storage','audit','prometheus','grafana']; print({name: socket.gethostbyname(name) for name in names})"
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/dns-check -n format-security --timeout=60s
kubectl logs pod/dns-check -n format-security
kubectl delete pod/dns-check -n format-security
```

Проверьте таблицу Audit:

```powershell
kubectl exec deployment/postgres -n format-security -- psql -U format_security -d format_security -c "\dt audit_events"
```

Список quarantine через безопасный Storage API доступен Gateway на странице <http://localhost:18080/quarantine>. Для прямой проверки внутри кластера:

```powershell
kubectl exec deployment/gateway -n format-security -- python -c "import urllib.request; print(urllib.request.urlopen('http://storage:8083/objects/quarantine').read().decode())"
```

При запущенном port-forward Prometheus проверьте target Audit:

```powershell
curl.exe -G --data-urlencode 'query=up{job="audit"}' http://localhost:19090/api/v1/query
```

Ожидаемое значение — `1`. Доступность Grafana можно проверить через API:

```powershell
curl.exe http://localhost:13000/api/health
```

## 6. Kubernetes E2E

При запущенном port-forward Gateway выполните из корня проекта:

```powershell
curl.exe -F "file=@services/validator/tests/samples/valid.json;type=application/json" http://localhost:18080/ingest
curl.exe -F "file=@services/validator/tests/samples/valid.csv;filename=telemetry.json;type=application/json" http://localhost:18080/ingest
curl.exe -F "file=@services/validator/tests/samples/broken.json;type=application/json" http://localhost:18080/ingest
curl.exe -F "file=@services/inspector/tests/samples/unknown.txt;filename=unknown.dat;type=application/octet-stream" http://localhost:18080/ingest
```

Ожидаемые результаты:

1. `valid.json` — `ACCEPT`, объект в `trusted`;
2. CSV с именем `telemetry.json` — `QUARANTINE`, причина `FORMAT_MISMATCH`;
3. `broken.json` — `REJECT`, причина `INVALID_JSON`;
4. `unknown.dat` — `REJECT`, причина `FORMAT_NOT_DETECTED`.

Проверьте страницы и API:

```powershell
curl.exe http://localhost:18080/health
curl.exe http://localhost:18080/history
curl.exe http://localhost:18080/quarantine
curl.exe http://localhost:18080/status
```

После E2E повторно проверьте `audit_events`, список quarantine, Prometheus `up{job="audit"}` и dashboard Grafana.

## 7. Обновление Grafana provisioning

После изменения исходных файлов в `infra/grafana` повторите обе команды `kubectl create configmap ... --dry-run=client`, затем перезапустите только Grafana:

```powershell
kubectl rollout restart deployment/grafana -n format-security
kubectl rollout status deployment/grafana -n format-security --timeout=180s
```

## 8. Minikube и kind

Основной сценарий — Docker Desktop Kubernetes. Для minikube или kind локальные образы нужно явно загрузить в кластер после сборки.

Пример для minikube:

```powershell
minikube image load format-security-gateway:local
minikube image load format-security-inspector:local
minikube image load format-security-validator:local
minikube image load format-security-storage:local
minikube image load format-security-audit:local
```

Пример для kind, где `<cluster-name>` — имя существующего кластера:

```powershell
kind load docker-image format-security-gateway:local format-security-inspector:local format-security-validator:local format-security-storage:local format-security-audit:local --name <cluster-name>
```

Также убедитесь, что кластер предоставляет стандартный StorageClass для PVC. Полная поддержка всех конфигураций minikube/kind в рамках учебного стенда не гарантируется.

## Хранение данных

PostgreSQL, MinIO и Grafana используют PVC по 1 GiB:

- `postgres-data`;
- `minio-data`;
- `grafana-data`.

Prometheus использует `emptyDir`: метрики сохраняются только в течение жизни pod. Для локального учебного мониторинга это намеренное упрощение.

## Диагностика

```powershell
kubectl describe pod -n format-security <pod-name>
kubectl logs -n format-security deployment/<deployment-name>
kubectl get events -n format-security --sort-by=.lastTimestamp
```

Если приложение получило `ImagePullBackOff`, убедитесь, что образ собран с точным тегом `:local` и доступен container runtime выбранного кластера. Способ загрузки зависит от provisioner и image store; для minikube/kind используйте загрузку образов из раздела выше.

## Удаление

```powershell
kubectl delete namespace format-security
```

Эта команда удаляет все namespaced-объекты стенда, включая PVC и данные, если политика StorageClass/PersistentVolume допускает их удаление.
