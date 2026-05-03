# Setup Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker | 29.x+ | [docs.docker.com](https://docs.docker.com/engine/install/ubuntu/) |
| kubectl | v1.35+ | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |
| Minikube | v1.38+ | [minikube.sigs.k8s.io](https://minikube.sigs.k8s.io/docs/start/) |
| Helm | v3.20+ | [helm.sh](https://helm.sh/docs/intro/install/) |
| Python | 3.11+ | apt install python3 |

## Step by Step Installation

### 1. System Update
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Docker
```bash
sudo apt install -y ca-certificates curl gnupg lsb-release
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io
sudo usermod -aG docker $USER && newgrp docker
```

### 3. kubectl
```bash
curl -LO "https://dl.k8s.io/release/$(curl -L -s \
  https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
```

### 4. Minikube
```bash
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
```

### 5. Helm
```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

## Troubleshooting

### Minikube memory warning
If you have less than 6GB RAM, start with:
```bash
minikube start --driver=docker --cpus=4 --memory=3500
```

### Kyverno webhook timeout
If pods get stuck waiting for webhook:
```bash
kubectl delete pods -n kyverno --all
```

### Engine 403 on ConfigMap
Ensure ClusterRole has configmaps permission:
```bash
kubectl get clusterrole zta-controller-role -o yaml | grep configmaps
```
