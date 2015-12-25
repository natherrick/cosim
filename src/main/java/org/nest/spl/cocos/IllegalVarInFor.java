/*
 * Copyright (c)  RWTH Aachen. All rights reserved.
 *
 * http://www.se-rwth.de/
 */
package org.nest.spl.cocos;

import com.google.common.base.Preconditions;
import de.monticore.symboltable.Scope;
import de.se_rwth.commons.logging.Log;
import org.nest.spl._ast.ASTFOR_Stmt;
import org.nest.spl._cocos.SPLASTFOR_StmtCoCo;
import org.nest.spl.symboltable.typechecking.TypeChecker;
import org.nest.symboltable.symbols.VariableSymbol;

import java.util.Optional;

import static com.google.common.base.Preconditions.checkArgument;

/**
 * Check that the type of the loop variable is an integer.
 *
 * @author (last commit) $$Author$$
 * @version $$Revision$$, $$Date$$
 * @since 0.0.1
 */
public class IllegalVarInFor implements SPLASTFOR_StmtCoCo {
  public static final String ERROR_CODE = "SPL_ILLEGAL_VAR_IN_FOR";

  private static final String ERROR_MSG_FORMAT = "The type of the iterator in a for-loop must be numeric and not: '%s' .";

  @Override
  public void check(final ASTFOR_Stmt astfor) {
    checkArgument(astfor.getEnclosingScope().isPresent(), "No scope assigned. Please, run symboltable creator.");
    final Scope scope = astfor.getEnclosingScope().get();

    String iterName = astfor.getVar();

    Optional<VariableSymbol> iter = scope.resolve(iterName, VariableSymbol.KIND);
    Preconditions.checkState(iter.isPresent());
    TypeChecker tc = new TypeChecker();
    if (!tc.checkNumber(iter.get().getType())) {
      Log.error(ERROR_CODE + ":" + String.format(ERROR_MSG_FORMAT, iter.get().getType()),
          astfor.get_SourcePositionEnd());
    }

  }

}
