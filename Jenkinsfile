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
            echo 'CI RESULT: SUCCESS'
        }

        failure {
            echo 'CI RESULT: FAILURE'
        }

        always {
            echo 'CI pipeline finished.'
        }
    }
}
