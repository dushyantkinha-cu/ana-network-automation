pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('Environment') {
            steps {
                sh '''
                    set -eu

                    echo "=== HOST ==="
                    hostname

                    echo
                    echo "=== WORKSPACE ==="
                    pwd

                    echo
                    echo "=== PYTHON ==="
                    python3 --version

                    echo
                    echo "=== GIT HEAD ==="
                    git log -1 --oneline
                '''
            }
        }

        stage('GitHub Status Pending') {
            steps {
                withCredentials([
                    string(
                        credentialsId: 'github-status-token',
                        variable: 'GITHUB_STATUS_TOKEN'
                    )
                ]) {
                    sh '''
                        set -eu

                        STATUS_SHA="$(git rev-parse HEAD)"

                        set +x
                        curl \
                            --fail-with-body \
                            --silent \
                            --show-error \
                            --request POST \
                            --header "Accept: application/vnd.github+json" \
                            --header "Authorization: Bearer $GITHUB_STATUS_TOKEN" \
                            --header "X-GitHub-Api-Version: 2026-03-10" \
                            "https://api.github.com/repos/dushyantkinha-cu/ana-network-automation/statuses/$STATUS_SHA" \
                            --data '{"state":"pending","description":"Jenkins CI is running","context":"jenkins/ci"}' \
                            >/dev/null
                        set -x

                        echo "PASS: GitHub status set to pending"
                    '''
                }
            }
        }

        stage('Python Environment') {
            steps {
                sh '''
                    set -eu

                    rm -rf .venv
                    python3 -m venv .venv

                    .venv/bin/python -m pip install \
                        --upgrade pip

                    .venv/bin/python -m pip install \
                        -r webapp/requirements-dev.txt
                '''
            }
        }

        stage('Python Syntax') {
            steps {
                sh '''
                    set -eu

                    .venv/bin/python -m compileall \
                        -q \
                        automation \
                        webapp \
                        tests

                    echo "PASS: Python syntax"
                '''
            }
        }

        stage('Regression Tests') {
            steps {
                sh '''
                    set -eu

                    .venv/bin/python -m pytest \
                        -q
                '''
            }
        }

        stage('Git Diff Check') {
            steps {
                sh '''
                    set -eu

                    git diff --check

                    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
                        echo "ERROR: CI modified tracked repository files."
                        git status --short
                        exit 1
                    fi

                    echo "PASS: repository remained clean"
                '''
            }
        }
    }

    post {
        success {
            withCredentials([
                string(
                    credentialsId: 'github-status-token',
                    variable: 'GITHUB_STATUS_TOKEN'
                )
            ]) {
                sh '''
                    set -eu

                    STATUS_SHA="$(git rev-parse HEAD)"

                    set +x
                    curl \
                        --fail-with-body \
                        --silent \
                        --show-error \
                        --request POST \
                        --header "Accept: application/vnd.github+json" \
                        --header "Authorization: Bearer $GITHUB_STATUS_TOKEN" \
                        --header "X-GitHub-Api-Version: 2026-03-10" \
                        "https://api.github.com/repos/dushyantkinha-cu/ana-network-automation/statuses/$STATUS_SHA" \
                        --data '{"state":"success","description":"Jenkins CI passed","context":"jenkins/ci"}' \
                        >/dev/null
                    set -x

                    echo "PASS: GitHub status set to success"
                '''
            }

            echo 'CI RESULT: SUCCESS'
        }

        failure {
            withCredentials([
                string(
                    credentialsId: 'github-status-token',
                    variable: 'GITHUB_STATUS_TOKEN'
                )
            ]) {
                sh '''
                    STATUS_SHA="$(git rev-parse HEAD)"

                    set +x
                    curl \
                        --fail-with-body \
                        --silent \
                        --show-error \
                        --request POST \
                        --header "Accept: application/vnd.github+json" \
                        --header "Authorization: Bearer $GITHUB_STATUS_TOKEN" \
                        --header "X-GitHub-Api-Version: 2026-03-10" \
                        "https://api.github.com/repos/dushyantkinha-cu/ana-network-automation/statuses/$STATUS_SHA" \
                        --data '{"state":"failure","description":"Jenkins CI failed","context":"jenkins/ci"}' \
                        >/dev/null
                    set -x

                    echo "GitHub status set to failure"
                '''
            }

            echo 'CI RESULT: FAILURE'
        }

        always {
            echo 'CI pipeline finished.'
        }
    }
}
