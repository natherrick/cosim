/*
 * Copyright (c) 2015 RWTH Aachen. All rights reserved.
 *
 * http://www.se-rwth.de/
 */
package org.nest.codegeneration.sympy;

import org.junit.Test;
import org.nest.ModelTestBase;
import org.nest.nestml._ast.ASTNESTMLCompilationUnit;
import org.nest.nestml._parser.NESTMLCompilationUnitMCParser;
import org.nest.nestml._symboltable.NESTMLScopeCreator;
import org.nest.symboltable.predefined.PredefinedTypesFactory;

import java.io.*;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Optional;

import static de.se_rwth.commons.Names.getPathFromPackage;
import static de.se_rwth.commons.Names.getQualifiedName;
import static org.junit.Assert.assertTrue;
import static org.nest.codegeneration.sympy.SymPyScriptGenerator.generateSympyODEAnalyzer;
import static org.nest.nestml._parser.NESTMLParserFactory.createNESTMLCompilationUnitMCParser;

/**
 * Tests that the solver script is generated from an ODE based model.
 *
 * @author plotnikov
 */
public class SymPyScriptGeneratorTest extends ModelTestBase {
  public static final String PATH_TO_PSC_MODEL
      = "src/test/resources/codegeneration/iaf_neuron_ode_module.nestml";
  public static final String PATH_TO_COND_MODEL
      = "src/test/resources/codegeneration/iaf_cond_alpha_module.nestml";
  private static final String OUTPUT_FOLDER = "target";

  @Test
  public void generateSymPySolverForPSCModel() throws IOException {
    generateScriptForModel(PATH_TO_PSC_MODEL);
  }

  @Test
  public void generateSymPySolverForCondModel() throws IOException {
    generateScriptForModel(PATH_TO_COND_MODEL);
  }

  private void generateScriptForModel(final String pathToModel) throws IOException {
    final NESTMLCompilationUnitMCParser p =
        createNESTMLCompilationUnitMCParser();
    final Optional<ASTNESTMLCompilationUnit> root = p.parse(pathToModel);

    assertTrue(root.isPresent());

    final NESTMLScopeCreator nestmlScopeCreator = new NESTMLScopeCreator(
        TEST_MODEL_PATH, typesFactory);
    nestmlScopeCreator.runSymbolTableCreator(root.get());

    final Optional<Path> generatedScript = generateSympyODEAnalyzer(
        root.get().getNeurons().get(0),
        Paths.get(OUTPUT_FOLDER));

    assertTrue(generatedScript.isPresent());
  }

}
