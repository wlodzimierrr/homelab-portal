#!/usr/bin/env python3
"""
Test script for Portal Wizard database addon scaffold generation.
Verifies that database addon files are correctly generated with all required manifests.
"""

import sys
from pathlib import Path

# Add the app directory to the path so we can import scaffold_service
sys.path.insert(0, str(Path(__file__).parent))

from app.scaffold_service import ScaffoldServiceInput, generate_gitops_new_files


def test_database_addon_postgres():
    """Test database addon with PostgreSQL variant."""
    print("=" * 80)
    print("TEST: Database addon with PostgreSQL")
    print("=" * 80)
    
    inp = ScaffoldServiceInput(
        name="my-app",
        description="My test application with database",
        image_repo="ghcr.io/example/my-app",
        repo_url="https://github.com/example/my-app",
        owner_email="dev@example.com",
        owner="Example Team",
        template="static-nginx",
        namespace="my-app",
        dev_host="my-app.dev.homelab.io",
        prod_host="my-app.homelab.io",
        workloads_repo_url="https://github.com/example/homelab-gitops",
        addon_database="postgres",
        addon_migration_command="alembic upgrade head",
    )
    
    files = generate_gitops_new_files(inp)
    
    # Check that database addon files are present
    required_files = [
        "apps/my-app/base/database-secret.yaml",
        "apps/my-app/base/database-statefulset.yaml",
        "apps/my-app/base/database-service.yaml",
        "apps/my-app/base/database-migration-job.yaml",
    ]
    
    print("\nGenerated files:")
    for filepath in sorted(files.keys()):
        print(f"  ✓ {filepath}")
    
    print("\nChecking required database addon files:")
    all_present = True
    for req_file in required_files:
        if req_file in files:
            print(f"  ✓ {req_file}")
        else:
            print(f"  ✗ MISSING: {req_file}")
            all_present = False
    
    # Check kustomization includes database resources
    kust_path = "apps/my-app/base/kustomization.yaml"
    if kust_path in files:
        kust_content = files[kust_path]
        print(f"\nKustomization resources:")
        for line in kust_content.split('\n'):
            if 'database' in line.lower():
                print(f"  ✓ {line.strip()}")
        
        if 'database-secret.yaml' in kust_content:
            print("  ✓ database-secret.yaml in resources")
        else:
            print("  ✗ database-secret.yaml NOT in resources")
            all_present = False
    
    # Check deployment includes database env vars
    deploy_path = "apps/my-app/base/deployment.yaml"
    if deploy_path in files:
        deploy_content = files[deploy_path]
        if 'DATABASE_URL' in deploy_content:
            print(f"\n✓ Deployment includes DATABASE_URL environment variable")
        else:
            print(f"\n✗ Deployment MISSING DATABASE_URL environment variable")
            all_present = False
        
        if 'POSTGRES_USER' in deploy_content:
            print(f"✓ Deployment includes POSTGRES_USER environment variable")
        else:
            print(f"✗ Deployment MISSING POSTGRES_USER environment variable")
            all_present = False
    
    print(f"\n{'PASS' if all_present else 'FAIL'}: PostgreSQL addon test")
    return all_present


def test_database_addon_mysql():
    """Test database addon with MySQL variant."""
    print("\n" + "=" * 80)
    print("TEST: Database addon with MySQL")
    print("=" * 80)
    
    inp = ScaffoldServiceInput(
        name="my-service",
        description="My service with MySQL database",
        image_repo="ghcr.io/example/my-service",
        repo_url="https://github.com/example/my-service",
        owner_email="dev@example.com",
        owner="Example Team",
        template="static-nginx",
        namespace="my-service",
        dev_host="my-service.dev.homelab.io",
        prod_host="my-service.homelab.io",
        workloads_repo_url="https://github.com/example/homelab-gitops",
        addon_database="mysql",
        addon_migration_command="mysql -u root -p -e 'USE app_db;'",
    )
    
    files = generate_gitops_new_files(inp)
    
    # Check that database addon files are present
    required_files = [
        "apps/my-service/base/database-secret.yaml",
        "apps/my-service/base/database-deployment.yaml",
        "apps/my-service/base/database-service.yaml",
        "apps/my-service/base/database-migration-job.yaml",
    ]
    
    print("\nGenerated files:")
    for filepath in sorted(files.keys()):
        if 'database' in filepath:
            print(f"  ✓ {filepath}")
    
    print("\nChecking required database addon files:")
    all_present = True
    for req_file in required_files:
        if req_file in files:
            print(f"  ✓ {req_file}")
        else:
            print(f"  ✗ MISSING: {req_file}")
            all_present = False
    
    # Check deployment includes database env vars
    deploy_path = "apps/my-service/base/deployment.yaml"
    if deploy_path in files:
        deploy_content = files[deploy_path]
        if 'DATABASE_URL' in deploy_content:
            print(f"\n✓ Deployment includes DATABASE_URL environment variable")
        else:
            print(f"\n✗ Deployment MISSING DATABASE_URL environment variable")
            all_present = False
        
        if 'MYSQL_DATABASE' in deploy_content:
            print(f"✓ Deployment includes MYSQL_DATABASE environment variable")
        else:
            print(f"✗ Deployment MISSING MYSQL_DATABASE environment variable")
            all_present = False
    
    print(f"\n{'PASS' if all_present else 'FAIL'}: MySQL addon test")
    return all_present


def test_no_database_addon():
    """Test scaffold generation without database addon (baseline)."""
    print("\n" + "=" * 80)
    print("TEST: No database addon (baseline)")
    print("=" * 80)
    
    inp = ScaffoldServiceInput(
        name="basic-app",
        description="Basic application without database",
        image_repo="ghcr.io/example/basic-app",
        repo_url="https://github.com/example/basic-app",
        owner_email="dev@example.com",
        owner="Example Team",
        template="static-nginx",
        namespace="basic-app",
        dev_host="basic-app.dev.homelab.io",
        prod_host="basic-app.homelab.io",
        workloads_repo_url="https://github.com/example/homelab-gitops",
    )
    
    files = generate_gitops_new_files(inp)
    
    # Database files should NOT be present
    db_files = [k for k in files.keys() if 'database' in k]
    
    if db_files:
        print(f"\n✗ FAIL: Database files should not be generated when addon_database is None")
        for f in db_files:
            print(f"  Unexpected: {f}")
        return False
    else:
        print(f"\n✓ PASS: No database files generated as expected")
        return True


if __name__ == "__main__":
    results = [
        test_database_addon_postgres(),
        test_database_addon_mysql(),
        test_no_database_addon(),
    ]
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("✓ ALL TESTS PASSED")
        sys.exit(0)
    else:
        print("✗ SOME TESTS FAILED")
        sys.exit(1)
