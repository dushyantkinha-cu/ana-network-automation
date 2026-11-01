pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    triggers {
        // Keep Jenkins private while automatically testing Git changes.
        pollSCM('H/5 * * * *')
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

                    .venv/bin/python -m pytest -q \
                        tests/test_arista_deployment.py \
                        tests/test_cisco_deployment.py \
                        tests/test_deployment_framework.py \
                        tests/test_nokia_deployment.py \
                        tests/test_webapp_baseline.py \
                        tests/test_validate_intent.py \
                        tests/test_netbox_inventory.py \
                        tests/test_render_config.py \
                        tests/test_device_connection.py \
                        tests/test_collect_live_configs.py \
                        tests/test_compare_goldens.py \
                        tests/test_promote_golden.py \
                        tests/test_report_drift.py \
                        tests/test_run_validation.py \
                        tests/test_webapp_changes_service.py \
                        tests/test_webapp_netbox_client.py \
                        tests/test_webapp_automation_service.py \
                        tests/test_webapp_inventory_service.py \
                        tests/test_webapp_jenkins_client.py \
                        tests/test_webapp_onboarding.py \
                        tests/test_lab9_ospf_template.py \
                        tests/test_lab9_ztp_bootstrap.py \
                        tests/test_lab9_monitoring_integration.py \
                        tests/test_webapp_sites.py \
                        tests/test_webapp_changes_router.py \
                        tests/test_webapp_automation_router.py \
                        tests/test_webapp_core_router.py \
                        --cov=automation \
                        --cov=webapp \
                        --cov-config=.coveragerc \
                        --cov-branch \
                        --cov-report=term \
                        --cov-report=xml:coverage.xml

                    .venv/bin/python scripts/coverage_audit.py \
                        coverage-audit.json | tee coverage-audit.txt
                '''

                archiveArtifacts artifacts: 'coverage.xml,coverage-audit.json,coverage-audit.txt'
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
